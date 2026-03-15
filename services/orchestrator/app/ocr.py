from __future__ import annotations

import io
import os
import shutil
from pathlib import Path

from pdf2image import convert_from_path
from pypdf import PdfReader, PdfWriter
import pytesseract


def resolve_poppler_path(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    if shutil.which("pdfinfo") or shutil.which("pdftoppm"):
        return None
    for candidate in ("/usr/bin", "/usr/local/bin", "/opt/homebrew/bin"):
        if os.path.isfile(os.path.join(candidate, "pdfinfo")) or os.path.isfile(
            os.path.join(candidate, "pdftoppm")
        ):
            return candidate
    return None


def resolve_tesseract_path(explicit: str | None = None) -> str | None:
    if explicit and os.path.isfile(explicit):
        return explicit
    discovered = shutil.which("tesseract")
    if discovered:
        return discovered
    for candidate in ("/usr/bin/tesseract", "/usr/local/bin/tesseract", "/opt/homebrew/bin/tesseract"):
        if os.path.isfile(candidate):
            return candidate
    return None


def create_tesseract_overlay_pdf(
    source_pdf: Path,
    output_pdf: Path,
    *,
    language: str,
    dpi: int,
    poppler_path: str | None = None,
    tesseract_cmd: str | None = None,
) -> Path:
    if not source_pdf.is_file():
        raise RuntimeError(f"Source PDF not found: {source_pdf}")

    resolved_tesseract = resolve_tesseract_path(tesseract_cmd)
    if not resolved_tesseract:
        raise RuntimeError("Tesseract not found. Set OCR_TESSERACT_CMD or install tesseract.")
    pytesseract.pytesseract.tesseract_cmd = resolved_tesseract

    resolved_poppler = resolve_poppler_path(poppler_path)

    try:
        page_count = len(PdfReader(str(source_pdf)).pages)
    except Exception as exc:
        raise RuntimeError(f"Failed to inspect PDF page count: {exc}") from exc
    if page_count <= 0:
        raise RuntimeError("Input PDF has no pages.")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    ocr_language = (language or "eng").strip() or "eng"

    for page_number in range(1, page_count + 1):
        try:
            images = convert_from_path(
                str(source_pdf),
                dpi=dpi,
                first_page=page_number,
                last_page=page_number,
                poppler_path=resolved_poppler,
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to rasterize PDF page {page_number}: {exc}") from exc
        if not images:
            raise RuntimeError(f"Rasterizer returned no image for page {page_number}.")

        try:
            pdf_bytes = pytesseract.image_to_pdf_or_hocr(
                images[0],
                extension="pdf",
                lang=ocr_language,
            )
        except Exception as exc:
            raise RuntimeError(f"Tesseract OCR failed on page {page_number}: {exc}") from exc

        try:
            overlay_page = PdfReader(io.BytesIO(pdf_bytes)).pages[0]
        except Exception as exc:
            raise RuntimeError(f"Failed to parse OCR output for page {page_number}: {exc}") from exc
        writer.add_page(overlay_page)

    try:
        with output_pdf.open("wb") as handle:
            writer.write(handle)
    except Exception as exc:
        raise RuntimeError(f"Failed to write OCR PDF {output_pdf}: {exc}") from exc

    return output_pdf
