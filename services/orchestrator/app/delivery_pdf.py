from __future__ import annotations

import io
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.schemas import BibliographicData

_COVER_FONT_FILE_PAIRS = (
    (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ),
    (
        Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ),
    (
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
    ),
    (
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ),
    (
        Path("/Library/Fonts/Arial.ttf"),
        Path("/Library/Fonts/Arial Bold.ttf"),
    ),
)


def prepend_delivery_cover_page(
    source_pdf: Path,
    output_pdf: Path,
    *,
    request_id: str,
    item_index: int,
    bibliographic_data: BibliographicData,
    order_date: datetime,
    delivery_date: datetime,
    language: str | None,
) -> Path:
    if not source_pdf.is_file():
        raise RuntimeError(f"Source PDF not found: {source_pdf}")

    cover_pdf = _build_cover_page_pdf(
        request_id=request_id,
        item_index=item_index,
        bibliographic_data=bibliographic_data,
        order_date=order_date,
        delivery_date=delivery_date,
        language=language,
    )

    source_reader = PdfReader(str(source_pdf))
    cover_reader = PdfReader(io.BytesIO(cover_pdf))
    writer = PdfWriter()
    writer.add_page(cover_reader.pages[0])
    for page in source_reader.pages:
        writer.add_page(page)

    source_metadata = source_reader.metadata or {}
    metadata = {
        key: value
        for key, value in source_metadata.items()
        if isinstance(key, str) and isinstance(value, str)
    }
    metadata.setdefault("/Title", bibliographic_data.title)
    metadata.setdefault("/Subject", _section_label(language, "document_delivery"))
    if metadata:
        writer.add_metadata(metadata)

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as handle:
        writer.write(handle)
    return output_pdf


def _build_cover_page_pdf(
    *,
    request_id: str,
    item_index: int,
    bibliographic_data: BibliographicData,
    order_date: datetime,
    delivery_date: datetime,
    language: str | None,
) -> bytes:
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=22 * mm,
        rightMargin=22 * mm,
        topMargin=22 * mm,
        bottomMargin=22 * mm,
    )
    styles = _styles()
    story = [
        Paragraph(_section_label(language, "document_delivery"), styles["title"]),
        Paragraph(
            escape(f"{_section_label(language, 'item')} {item_index + 1}"),
            styles["subtitle"],
        ),
        Spacer(1, 10),
        Table(
            [
                _table_row(language, "request_id", request_id, styles),
                _table_row(language, "order_date", _format_date(order_date, language), styles),
                _table_row(language, "delivery_date", _format_date(delivery_date, language), styles),
            ],
            colWidths=[40 * mm, 120 * mm],
            style=_table_style(),
        ),
        Spacer(1, 14),
        Paragraph(_section_label(language, "bibliographic_data"), styles["heading"]),
        Spacer(1, 6),
        Table(_bibliographic_rows(bibliographic_data, language, styles), colWidths=[40 * mm, 120 * mm], style=_table_style()),
    ]
    document.build(story)
    return buffer.getvalue()


def _bibliographic_rows(
    bibliographic_data: BibliographicData,
    language: str | None,
    styles: dict[str, ParagraphStyle],
) -> list[list[Paragraph]]:
    rows: list[tuple[str, str]] = []
    creators = "; ".join(bibliographic_data.creators)
    editors = "; ".join(bibliographic_data.editors)
    values = [
        ("creators", creators),
        ("editors", editors),
        ("title", bibliographic_data.title),
        ("publication_title", bibliographic_data.publication_title),
        ("year", bibliographic_data.year),
        ("volume", bibliographic_data.volume or ""),
        ("issue", bibliographic_data.issue or ""),
        ("pages", bibliographic_data.pages or ""),
        ("publisher", bibliographic_data.publisher or ""),
        ("place", bibliographic_data.place or ""),
        ("series", bibliographic_data.series or ""),
        ("edition", bibliographic_data.edition or ""),
        ("doi", bibliographic_data.doi or ""),
        ("isbn", bibliographic_data.isbn or ""),
    ]
    for key, value in values:
        cleaned = value.strip()
        if cleaned:
            rows.append((key, cleaned))
    return [_table_row(language, key, value, styles) for key, value in rows]


def _table_row(
    language: str | None,
    key: str,
    value: str,
    styles: dict[str, ParagraphStyle],
) -> list[Paragraph]:
    return [
        Paragraph(escape(_section_label(language, key)), styles["label"]),
        Paragraph(escape(value), styles["body"]),
    ]


def _table_style() -> TableStyle:
    return TableStyle(
        [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
    )


def _styles() -> dict[str, ParagraphStyle]:
    sample_styles = getSampleStyleSheet()
    regular_font, bold_font = _cover_font_names()
    return {
        "title": ParagraphStyle(
            "CoverTitle",
            parent=sample_styles["Heading1"],
            fontName=bold_font,
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#111827"),
            spaceAfter=0,
        ),
        "subtitle": ParagraphStyle(
            "CoverSubtitle",
            parent=sample_styles["BodyText"],
            fontName=regular_font,
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#4b5563"),
            spaceAfter=0,
        ),
        "heading": ParagraphStyle(
            "SectionHeading",
            parent=sample_styles["Heading2"],
            fontName=bold_font,
            fontSize=12,
            leading=15,
            textColor=colors.HexColor("#111827"),
            spaceAfter=0,
        ),
        "label": ParagraphStyle(
            "Label",
            parent=sample_styles["BodyText"],
            fontName=bold_font,
            fontSize=9.5,
            leading=12,
            textColor=colors.HexColor("#111827"),
            spaceAfter=0,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=sample_styles["BodyText"],
            fontName=regular_font,
            fontSize=9.5,
            leading=12.5,
            textColor=colors.HexColor("#111827"),
            spaceAfter=0,
        ),
    }


@lru_cache(maxsize=1)
def _cover_font_names() -> tuple[str, str]:
    for index, (regular_path, bold_path) in enumerate(_COVER_FONT_FILE_PAIRS):
        if not regular_path.is_file() or not bold_path.is_file():
            continue
        regular_name = f"DeliveryCoverSans-{index}"
        bold_name = f"DeliveryCoverSans-Bold-{index}"
        try:
            pdfmetrics.registerFont(TTFont(regular_name, str(regular_path)))
            pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
            return regular_name, bold_name
        except Exception:
            continue
    return "Helvetica", "Helvetica-Bold"


def _format_date(value: datetime, language: str | None) -> str:
    if _normalize_language(language) == "en":
        return value.date().isoformat()
    return value.strftime("%d.%m.%Y")


def _normalize_language(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized.startswith("de"):
        return "de"
    if normalized.startswith("pl"):
        return "pl"
    if normalized.startswith("en"):
        return "en"
    return "de"


def _section_label(language: str | None, key: str) -> str:
    labels = {
        "de": {
            "document_delivery": "Dokumentlieferung",
            "item": "Titel",
            "request_id": "Bestellung",
            "order_date": "Bestelldatum",
            "delivery_date": "Lieferdatum",
            "bibliographic_data": "Bibliographische Angaben",
            "creators": "Autor:innen",
            "editors": "Herausgeber:innen",
            "title": "Titel",
            "publication_title": "Quelle",
            "year": "Jahr",
            "volume": "Band",
            "issue": "Heft",
            "pages": "Seiten",
            "publisher": "Verlag",
            "place": "Ort",
            "series": "Reihe",
            "edition": "Auflage",
            "doi": "DOI",
            "isbn": "ISBN",
        },
        "en": {
            "document_delivery": "Document delivery",
            "item": "Item",
            "request_id": "Request",
            "order_date": "Order date",
            "delivery_date": "Delivery date",
            "bibliographic_data": "Bibliographic data",
            "creators": "Authors",
            "editors": "Editors",
            "title": "Title",
            "publication_title": "Source",
            "year": "Year",
            "volume": "Volume",
            "issue": "Issue",
            "pages": "Pages",
            "publisher": "Publisher",
            "place": "Place",
            "series": "Series",
            "edition": "Edition",
            "doi": "DOI",
            "isbn": "ISBN",
        },
        "pl": {
            "document_delivery": "Dostarczenie dokumentu",
            "item": "Pozycja",
            "request_id": "Zamowienie",
            "order_date": "Data zamowienia",
            "delivery_date": "Data dostawy",
            "bibliographic_data": "Dane bibliograficzne",
            "creators": "Autorzy",
            "editors": "Redaktorzy",
            "title": "Tytul",
            "publication_title": "Zrodlo",
            "year": "Rok",
            "volume": "Tom",
            "issue": "Numer",
            "pages": "Strony",
            "publisher": "Wydawca",
            "place": "Miejsce",
            "series": "Seria",
            "edition": "Wydanie",
            "doi": "DOI",
            "isbn": "ISBN",
        },
    }
    normalized_language = _normalize_language(language)
    return labels.get(normalized_language, labels["de"]).get(key, key)
