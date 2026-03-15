from __future__ import annotations

import io
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from langdetect import DetectorFactory, LangDetectException, detect_langs
from pdf2image import convert_from_path
from pypdf import PdfReader, PdfWriter
import pytesseract

DetectorFactory.seed = 0


@dataclass(frozen=True)
class OcrOverlayResult:
    output_pdf: Path
    language_bundle: str
    detected_language: str | None

LANGUAGE_BUNDLES: dict[str, str] = {
    "de": "deu+eng+fra+nld",
    "en": "eng+deu+fra+nld",
    "pl": "pol+eng+deu+ces+slk",
    "cs": "ces+slk+eng+deu+pol",
    "sk": "slk+ces+eng+deu+pol",
    "sl": "slv+hrv+eng+deu+ita",
    "hr": "hrv+srp+slv+eng+deu",
    "hu": "hun+eng+deu+ron",
    "ro": "ron+eng+deu+hun",
    "bg": "bul+srp+mkd+eng+deu",
    "sr": "srp+hrv+bul+eng+deu",
    "mk": "mkd+bul+srp+eng+deu",
    "uk": "ukr+rus+pol+eng+deu",
    "ru": "rus+ukr+bel+eng+deu",
    "be": "bel+rus+ukr+eng+deu",
    "lt": "lit+lav+pol+eng+deu",
    "lv": "lav+lit+est+eng+deu",
    "et": "est+lav+lit+eng+deu",
    "fr": "fra+eng+deu+nld+ita",
    "nl": "nld+eng+deu+fra",
    "es": "spa+cat+por+eng+fra",
    "pt": "por+spa+cat+eng+fra",
    "it": "ita+fra+spa+eng+deu",
    "ca": "cat+spa+por+eng+fra",
    "da": "dan+swe+nor+eng+deu",
    "fi": "fin+swe+eng+deu",
    "sv": "swe+dan+nor+eng+deu",
    "no": "nor+swe+dan+eng+deu",
    "el": "ell+eng+deu+fra",
}


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


def _render_pdf_page(
    source_pdf: Path,
    page_number: int,
    *,
    dpi: int,
    poppler_path: str | None,
):
    try:
        images = convert_from_path(
            str(source_pdf),
            dpi=dpi,
            first_page=page_number,
            last_page=page_number,
            poppler_path=poppler_path,
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to rasterize PDF page {page_number}: {exc}") from exc
    if not images:
        raise RuntimeError(f"Rasterizer returned no image for page {page_number}.")
    return images[0]


def detect_tesseract_language_bundle(
    source_pdf: Path,
    *,
    dpi: int,
    seed_language: str,
    sample_pages: int,
    poppler_path: str | None = None,
    tesseract_cmd: str | None = None,
    fallback_language: str = "eng+deu+pol",
) -> tuple[str, str | None]:
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
        return fallback_language

    text_chunks: list[str] = []
    for page_number in range(1, min(page_count, max(sample_pages, 1)) + 1):
        image = _render_pdf_page(
            source_pdf,
            page_number,
            dpi=dpi,
            poppler_path=resolved_poppler,
        )
        try:
            sample_text = pytesseract.image_to_string(image, lang=seed_language)
        except Exception:
            continue
        sample_text = " ".join(sample_text.split())
        if sample_text:
            text_chunks.append(sample_text)

    if not text_chunks:
        return fallback_language

    try:
        candidates = detect_langs(" ".join(text_chunks))
    except LangDetectException:
        return fallback_language, None
    if not candidates:
        return fallback_language, None

    detected_iso = candidates[0].lang
    return LANGUAGE_BUNDLES.get(detected_iso, fallback_language), detected_iso


def create_tesseract_overlay_pdf(
    source_pdf: Path,
    output_pdf: Path,
    *,
    language: str,
    dpi: int,
    language_mode: str = "manual",
    detect_seed_language: str | None = None,
    detect_sample_pages: int = 2,
    poppler_path: str | None = None,
    tesseract_cmd: str | None = None,
) -> OcrOverlayResult:
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
    detected_language: str | None = None
    if (language_mode or "manual").strip().lower() == "auto":
        ocr_language, detected_language = detect_tesseract_language_bundle(
            source_pdf,
            dpi=dpi,
            seed_language=(detect_seed_language or ocr_language).strip() or ocr_language,
            sample_pages=detect_sample_pages,
            poppler_path=resolved_poppler,
            tesseract_cmd=resolved_tesseract,
            fallback_language=ocr_language,
        )

    for page_number in range(1, page_count + 1):
        image = _render_pdf_page(
            source_pdf,
            page_number,
            dpi=dpi,
            poppler_path=resolved_poppler,
        )

        try:
            pdf_bytes = pytesseract.image_to_pdf_or_hocr(
                image,
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

    return OcrOverlayResult(
        output_pdf=output_pdf,
        language_bundle=ocr_language,
        detected_language=detected_language,
    )
