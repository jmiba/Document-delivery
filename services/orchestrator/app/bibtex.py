from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas import BibliographicData

ENTRY_RE = re.compile(r"@\s*(?P<type>[A-Za-z]+)\s*\{\s*([^,]+)\s*,(?P<body>.*)\}\s*$", re.DOTALL)
FIELD_RE = re.compile(
    r"(?P<key>[A-Za-z][A-Za-z0-9_-]*)\s*=\s*(?P<value>\{(?:[^{}]|(?:\{[^{}]*\}))*\}|\"(?:[^\"\\\\]|\\\\.)*\"|[^,\n]+)",
    re.DOTALL,
)


def parse_bibtex_entry(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        raise ValueError("BibTeX payload is empty.")

    match = ENTRY_RE.match(text)
    if not match:
        raise ValueError("Invalid BibTeX entry.")

    entry_type = match.group("type").strip().lower()
    fields = _parse_fields(match.group("body"))

    item_type = _map_item_type(entry_type)
    title = fields.get("title", "")
    publication_title = _container_title(item_type, fields)
    year = _extract_year(fields)

    return {
        "item_type": item_type,
        "title": title,
        "creators": _parse_people(fields.get("author")),
        "editors": _parse_people(fields.get("editor")),
        "publication_title": publication_title,
        "year": year,
        "volume": _clean(fields.get("volume")) or None,
        "issue": _clean(fields.get("number")) or _clean(fields.get("issue")) or None,
        "pages": _normalize_pages(fields.get("pages")),
        "doi": _clean(fields.get("doi")) or None,
        "publisher": _clean(fields.get("publisher")) or None,
        "place": _clean(fields.get("address")) or _clean(fields.get("location")) or None,
        "series": _clean(fields.get("series")) or None,
        "edition": _clean(fields.get("edition")) or None,
        "isbn": _clean(fields.get("isbn")) or None,
        "language": _clean(fields.get("langid")) or _clean(fields.get("language")) or None,
        "abstract_note": _clean(fields.get("abstract")) or None,
    }


def _parse_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in FIELD_RE.finditer(body):
        key = match.group("key").strip().lower()
        value = _strip_wrapping(match.group("value").strip())
        value = _normalize_bibtex_text(value)
        fields[key] = _collapse_whitespace(value)
    return fields


def _strip_wrapping(value: str) -> str:
    if len(value) >= 2 and ((value[0] == "{" and value[-1] == "}") or (value[0] == '"' and value[-1] == '"')):
        return value[1:-1].strip()
    return value.strip()


def _collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _normalize_bibtex_text(value: str) -> str:
    cleaned = value or ""
    # Remove BibTeX protective braces while preserving their contents.
    cleaned = cleaned.replace("{", "").replace("}", "")
    # Unescape a small set of common BibTeX sequences that should survive as plain text.
    cleaned = cleaned.replace(r"\&", "&")
    return cleaned.strip()


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _extract_year(fields: dict[str, str]) -> str:
    for key in ("year", "date"):
        raw = fields.get(key, "")
        match = re.search(r"\b(1[5-9]\d{2}|20\d{2}|21\d{2})\b", raw)
        if match:
            return match.group(1)
    return ""


def _parse_people(value: str | None) -> list[str]:
    if not value:
        return []
    result: list[str] = []
    for person in re.split(r"\s+and\s+", value.strip(), flags=re.IGNORECASE):
        cleaned = _clean(person)
        if not cleaned:
            continue
        if "," in cleaned:
            last, first = [part.strip() for part in cleaned.split(",", 1)]
            result.append(f"{last}, {first}" if first else last)
            continue
        parts = cleaned.split()
        if len(parts) >= 2:
            result.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
        else:
            result.append(cleaned)
    return result


def _normalize_pages(value: str | None) -> str | None:
    cleaned = _clean(value)
    if not cleaned:
        return None
    return cleaned.replace("--", "-")


def _map_item_type(entry_type: str) -> str:
    mapping = {
        "article": "journalArticle",
        "incollection": "bookSection",
        "inbook": "bookSection",
        "book": "book",
        "inproceedings": "conferencePaper",
        "conference": "conferencePaper",
        "proceedings": "conferencePaper",
    }
    return mapping.get(entry_type, "journalArticle")


def _container_title(item_type: str, fields: dict[str, str]) -> str:
    if item_type == "journalArticle":
        return _clean(fields.get("journal")) or _clean(fields.get("journaltitle"))
    if item_type in {"bookSection", "conferencePaper"}:
        return _clean(fields.get("booktitle")) or _clean(fields.get("maintitle"))
    if item_type == "book":
        return _clean(fields.get("title"))
    return _clean(fields.get("booktitle")) or _clean(fields.get("journal")) or _clean(fields.get("journaltitle"))


def export_bibtex_entry(bib: "BibliographicData", key: str | None = None) -> str:
    entry_type = _entry_type_for_item(bib.item_type)
    entry_key = _safe_key(key or _default_entry_key(bib))
    fields: list[tuple[str, str]] = []

    if bib.creators:
        fields.append(("author", " and ".join(_format_person_for_bibtex(person) for person in bib.creators)))
    if bib.editors:
        fields.append(("editor", " and ".join(_format_person_for_bibtex(person) for person in bib.editors)))

    fields.append(("title", bib.title))

    if entry_type == "article":
        fields.append(("journal", bib.publication_title))
    elif entry_type in {"incollection", "inproceedings"}:
        fields.append(("booktitle", bib.publication_title))
    elif entry_type == "book":
        if bib.series:
            fields.append(("series", bib.series))
        if bib.publication_title and bib.publication_title != bib.title:
            fields.append(("maintitle", bib.publication_title))

    fields.append(("year", bib.year))

    optional_fields = [
        ("volume", bib.volume),
        ("number", bib.issue),
        ("pages", _bibtex_pages(bib.pages)),
        ("doi", bib.doi),
        ("publisher", bib.publisher),
        ("address", bib.place),
        ("series", bib.series if entry_type != "book" else None),
        ("edition", bib.edition),
        ("isbn", bib.isbn),
        ("language", bib.language),
        ("abstract", bib.abstract_note),
    ]
    for field_name, value in optional_fields:
        cleaned = _clean(value)
        if cleaned:
            fields.append((field_name, cleaned))

    lines = [f"@{entry_type}{{{entry_key},"]
    for index, (field_name, value) in enumerate(fields):
        suffix = "," if index < len(fields) - 1 else ""
        lines.append(f"  {field_name} = {{{_escape_bibtex_value(value)}}}{suffix}")
    lines.append("}")
    return "\n".join(lines)


def _entry_type_for_item(item_type: str | None) -> str:
    normalized = (item_type or "").strip()
    mapping = {
        "journalArticle": "article",
        "bookSection": "incollection",
        "book": "book",
        "conferencePaper": "inproceedings",
    }
    return mapping.get(normalized, "misc")


def _default_entry_key(bib: "BibliographicData") -> str:
    creator = bib.creators[0] if bib.creators else "item"
    last_name = creator.split(",", 1)[0].strip() if "," in creator else creator.split()[-1].strip()
    year = _clean(bib.year) or "n.d."
    title_token = re.sub(r"[^A-Za-z0-9]+", "", (bib.title or "").split(" ", 1)[0]) or "entry"
    return f"{last_name}{year}{title_token}"


def _safe_key(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9:_-]+", "", value or "")
    return safe or "entry"


def _format_person_for_bibtex(value: str) -> str:
    cleaned = _clean(value)
    if not cleaned:
        return ""
    return cleaned


def _bibtex_pages(value: str | None) -> str | None:
    cleaned = _clean(value)
    if not cleaned:
        return None
    return cleaned.replace("-", "--")


def _escape_bibtex_value(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
