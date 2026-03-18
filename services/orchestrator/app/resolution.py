from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree as ET

import requests

from app.config import settings
from app.schemas import BibliographicData, NormalizationResult, ResolutionEvidence


MIN_TITLE_SCORE_FOR_DOI_MATCH = 0.55
MIN_CONTAINER_SCORE = 0.5


@dataclass
class ResolutionMatch:
    source: str
    status: str
    score: float
    explanation: str
    candidate: BibliographicData | None = None
    title_score: float = 0.0
    container_score: float = 0.0
    year_match: bool = False


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _string_value(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        for item in value:
            resolved = _string_value(item)
            if resolved:
                return resolved
        return ""
    if isinstance(value, dict):
        for key in ("label", "name", "value", "id"):
            resolved = _string_value(value.get(key))
            if resolved:
                return resolved
        return ""
    return str(value).strip() if value is not None else ""


def _normalize_text(value: str) -> str:
    return (
        value.casefold()
        .replace("'", "")
        .replace('"', "")
        .replace("’", "")
        .replace("`", "")
    )


def _normalize_doi(raw: str | None) -> str:
    if not raw:
        return ""
    doi = raw.strip()
    lowered = doi.casefold()
    for prefix in ("https://doi.org/", "http://doi.org/"):
        if lowered.startswith(prefix):
            doi = doi[len(prefix):]
            break
    if doi.casefold().startswith("doi:"):
        doi = doi[4:]
    return doi.strip()


def _dice_coefficient(left: str, right: str) -> float:
    left_normalized = "".join(ch for ch in _normalize_text(left) if ch.isalnum())
    right_normalized = "".join(ch for ch in _normalize_text(right) if ch.isalnum())
    if not left_normalized or not right_normalized:
        return 0.0
    if left_normalized == right_normalized:
        return 1.0
    if len(left_normalized) < 2 or len(right_normalized) < 2:
        return 0.0

    bigrams: dict[str, int] = {}
    for idx in range(len(left_normalized) - 1):
        bigram = left_normalized[idx:idx + 2]
        bigrams[bigram] = bigrams.get(bigram, 0) + 1

    intersection = 0
    for idx in range(len(right_normalized) - 1):
        bigram = right_normalized[idx:idx + 2]
        count = bigrams.get(bigram, 0)
        if count > 0:
            bigrams[bigram] = count - 1
            intersection += 1

    return (2 * intersection) / ((len(left_normalized) - 1) + (len(right_normalized) - 1))


def _first_author_last_name(bib: BibliographicData) -> str:
    if not bib.creators:
        return ""
    author = bib.creators[0].strip()
    if not author:
        return ""
    if "," in author:
        return _normalize_text(author.split(",", 1)[0].strip())
    return _normalize_text(author.split()[-1])


def _year_from_text(value: str | None) -> str:
    import re

    match = re.search(r"\b(1[5-9]\d{2}|20\d{2}|21\d{2})\b", value or "")
    return match.group(1) if match else ""


def _normalize_pages(value: str | None) -> str | None:
    cleaned = _clean(value)
    if not cleaned:
        return None
    return cleaned.replace("--", "-")


def _crossref_people(people: list[dict] | None) -> list[str]:
    result: list[str] = []
    for person in people or []:
        given = _clean(person.get("given"))
        family = _clean(person.get("family"))
        literal = _clean(person.get("literal"))
        if family and given:
            result.append(f"{family}, {given}")
        elif family:
            result.append(family)
        elif literal:
            result.append(literal)
    return result


def _search_title_for_catalog(bib: BibliographicData) -> str:
    if bib.item_type == "bookSection" and bib.publication_title:
        return bib.publication_title
    return bib.title


def _build_lobid_query(bib: BibliographicData) -> str:
    title = _search_title_for_catalog(bib)
    tokens = [_normalize_text(part) for part in title.split() if len(_normalize_text(part)) >= 3]
    title_parts = [f"title:{token}" for token in tokens[:4] if token]
    clauses = [f"({' AND '.join(title_parts)})"] if title_parts else []
    author_last_name = _first_author_last_name(bib)
    if author_last_name and bib.item_type != "bookSection":
        clauses.append(f"contribution.agent.label:{author_last_name}")
    return " AND ".join(clauses)


def _extract_isbn_from_identifiers(identifiers: list[object]) -> str | None:
    import re

    for identifier in identifiers:
        if isinstance(identifier, dict):
            raw_value = (
                identifier.get("value")
                or identifier.get("id")
                or identifier.get("label")
                or identifier.get("identifier")
                or ""
            )
        else:
            raw_value = str(identifier or "")
        cleaned = re.sub(r"[^0-9Xx]", "", raw_value.replace("urn:isbn:", "").replace("isbn:", ""))
        if len(cleaned) in {10, 13}:
            return cleaned.upper()
    return None


def _should_hide_evidence_match(bib: BibliographicData, match: ResolutionMatch) -> bool:
    return (
        bib.item_type == "bookSection"
        and match.source in {"crossref", "openalex"}
        and match.status == "not_found"
        and match.score == 0.0
        and match.explanation.startswith("type mismatch")
    )


def _parse_sru_dc_records(xml_text: str) -> list[dict]:
    ns_dc = "{http://purl.org/dc/elements/1.1/}"
    ns_srw = "{http://www.loc.gov/zing/srw/}"
    root = ET.fromstring(xml_text)
    parsed: list[dict] = []
    for record in root.findall(".//{*}record"):
        identifiers = [
            (node.text or "").strip()
            for node in record.findall(f".//{ns_dc}identifier")
            if (node.text or "").strip()
        ]
        title = (record.findtext(f".//{ns_dc}title") or "").strip()
        creator = (record.findtext(f".//{ns_dc}creator") or "").strip()
        date = (record.findtext(f".//{ns_dc}date") or "").strip()
        publisher = (record.findtext(f".//{ns_dc}publisher") or "").strip()
        record_id = (record.findtext(f".//{ns_srw}recordIdentifier") or record.findtext(".//{*}recordIdentifier") or "").strip()
        if not any([title, creator, date, publisher, identifiers]):
            continue
        parsed.append(
            {
                "title": title,
                "creator": creator,
                "date": date,
                "publisher": publisher,
                "identifiers": identifiers,
                "record_id": record_id or None,
            }
        )
    return parsed


def _score_candidate(
    original: BibliographicData,
    *,
    title: str | None = None,
    doi: str | None = None,
    year: str | None = None,
    first_author_last_name: str | None = None,
    container_title: str | None = None,
    trust_ref_doi: bool = True,
) -> tuple[float, bool, float, float]:
    original_doi = _normalize_doi(original.doi)
    candidate_doi = _normalize_doi(doi)
    title_score = _dice_coefficient(original.title or "", title or "")
    container_score = _dice_coefficient(original.publication_title or "", container_title or "")

    if trust_ref_doi and original_doi and candidate_doi:
        if original_doi == candidate_doi:
            return 1.0, False, title_score, container_score
        return 0.0, True, title_score, container_score

    author_score = 0.0
    original_last_name = _first_author_last_name(original)
    if original_last_name and first_author_last_name:
        author_score = 1.0 if original_last_name == _normalize_text(first_author_last_name) else 0.0

    year_score = 1.0 if original.year and year and original.year == year else 0.0

    weighted_parts: list[tuple[float, float]] = []
    if original.title and title:
        weighted_parts.append((title_score, 0.6))
    if original.publication_title and container_title:
        weighted_parts.append((container_score, 0.2))
    if original_last_name and first_author_last_name:
        weighted_parts.append((author_score, 0.15))
    if original.year and year:
        weighted_parts.append((year_score, 0.05))

    if not weighted_parts:
        return 0.0, False, title_score, container_score

    total_weight = sum(weight for _, weight in weighted_parts)
    score = sum(score * weight for score, weight in weighted_parts) / total_weight
    return score, False, title_score, container_score


def _has_secondary_signal(
    original: BibliographicData,
    *,
    container_score: float,
    year: str | None = None,
    first_author_last_name: str | None = None,
) -> bool:
    author_match = bool(
        _first_author_last_name(original)
        and first_author_last_name
        and _first_author_last_name(original) == _normalize_text(first_author_last_name)
    )
    year_match = bool(original.year and year and original.year == year)
    container_match = bool(original.publication_title and container_score >= MIN_CONTAINER_SCORE)
    return author_match or year_match or container_match


def _make_bib(
    fallback: BibliographicData,
    *,
    title: str | None = None,
    creators: list[str] | None = None,
    editors: list[str] | None = None,
    publication_title: str | None = None,
    year: str | None = None,
    volume: str | None = None,
    issue: str | None = None,
    pages: str | None = None,
    doi: str | None = None,
    publisher: str | None = None,
    place: str | None = None,
    series: str | None = None,
    edition: str | None = None,
    isbn: str | None = None,
    abstract_note: str | None = None,
    sparse: bool = False,
) -> BibliographicData:
    item_type = fallback.item_type
    resolved_issue = issue if item_type == "journalArticle" else None
    if sparse:
        return BibliographicData(
            item_type=item_type,
            title=title or "",
            creators=creators or [],
            editors=editors or [],
            publication_title=publication_title or "",
            year=year or "",
            volume=volume,
            issue=resolved_issue,
            pages=pages,
            doi=_normalize_doi(doi) or None,
            publisher=publisher,
            place=place,
            series=series,
            edition=edition,
            isbn=isbn,
            language=None,
            abstract_note=abstract_note,
        )
    return BibliographicData(
        item_type=item_type,
        title=title or fallback.title,
        creators=creators or fallback.creators,
        editors=editors or fallback.editors,
        publication_title=publication_title or fallback.publication_title,
        year=year or fallback.year,
        volume=volume or fallback.volume,
        issue=resolved_issue if resolved_issue is not None else fallback.issue if item_type == "journalArticle" else None,
        pages=pages or fallback.pages,
        doi=_normalize_doi(doi) or fallback.doi,
        publisher=publisher or fallback.publisher,
        place=place or fallback.place,
        series=series or fallback.series,
        edition=edition or fallback.edition,
        isbn=isbn or fallback.isbn,
        language=fallback.language,
        abstract_note=abstract_note or fallback.abstract_note,
    )


def _source_type_matches(item_type: str, source_type: str | None) -> bool:
    normalized_source_type = _clean(source_type).casefold()
    if not normalized_source_type:
        return True
    if item_type == "journalArticle":
        return normalized_source_type in {"journal-article", "article", "journal_article"}
    if item_type == "bookSection":
        return normalized_source_type in {
            "book-chapter",
            "book-section",
            "book_part",
            "book-part",
            "chapter",
            "reference-entry",
        }
    return True


def _apply_candidate(
    original: BibliographicData,
    candidate: BibliographicData,
    *,
    overwrite: bool,
    overwrite_authors: bool = False,
) -> BibliographicData:
    data = original.model_dump()
    candidate_data = candidate.model_dump()
    for field_name, value in candidate_data.items():
        if field_name == "item_type":
            continue
        if isinstance(value, list):
            if value and (
                overwrite
                or (field_name == "creators" and overwrite_authors)
                or not data.get(field_name)
            ):
                data[field_name] = value
            continue
        if value and (overwrite or not data.get(field_name)):
            data[field_name] = value
    data["item_type"] = original.item_type
    if original.item_type != "journalArticle":
        data["issue"] = None
    return BibliographicData(**data)


def _validation_sort_key(priorities: dict[str, int], match: ResolutionMatch) -> tuple[float, int]:
    return (-match.score, priorities.get(match.source, 100))


def _allow_author_overwrite(original: BibliographicData, match: ResolutionMatch) -> bool:
    if not match.candidate or not match.candidate.creators:
        return False
    if match.candidate.creators == original.creators:
        return False
    return (
        match.score >= 0.8
        and match.title_score >= 0.9
        and match.container_score >= 0.75
        and match.year_match
    )


def _best_author_enrichment_match(original: BibliographicData, matches: list[ResolutionMatch]) -> ResolutionMatch | None:
    eligible_sources = {"crossref", "openalex"}
    candidates = []
    for match in matches:
        if (
            match.source in eligible_sources
            and match.status == "validated"
            and match.candidate is not None
            and match.candidate.creators
            and (
                _allow_author_overwrite(original, match)
                or (
                    match.score >= 0.8
                    and match.title_score >= 0.9
                    and (match.container_score >= 0.75 or match.year_match)
                )
            )
        ):
            candidates.append(match)
    if not candidates:
        return None
    candidates.sort(key=lambda match: (-match.score, self_or_priority_placeholder(match.source)))
    return candidates[0]


def self_or_priority_placeholder(source: str) -> int:
    if source == "crossref":
        return 0
    if source == "openalex":
        return 1
    return 100


class CrossrefResolver:
    def __init__(self) -> None:
        self.base = "https://api.crossref.org/works"
        self.mailto = settings.crossref_mailto

    def resolve(self, bib: BibliographicData) -> ResolutionMatch:
        headers = {"User-Agent": f"DocumentDelivery/1.0 ({self.mailto or 'no-mailto'})"}
        doi = _normalize_doi(bib.doi)
        if doi:
            response = requests.get(f"{self.base}/{doi}", headers=headers, timeout=20)
            if response.status_code == 404:
                return ResolutionMatch("crossref", "not_found", 0.0, "DOI not found")
            if not response.ok:
                return ResolutionMatch("crossref", "error", 0.0, f"request failed (HTTP {response.status_code})")
            item = response.json().get("message", {})
            return self._match_item(bib, item, trust_ref_doi=True)

        params = {"query.bibliographic": bib.title, "rows": 5}
        if self.mailto:
            params["mailto"] = self.mailto
        response = requests.get(self.base, headers=headers, params=params, timeout=20)
        if not response.ok:
            return ResolutionMatch("crossref", "error", 0.0, f"search failed (HTTP {response.status_code})")
        items = response.json().get("message", {}).get("items", [])
        if not items:
            return ResolutionMatch("crossref", "not_found", 0.0, "no results")

        best: ResolutionMatch | None = None
        for item in items:
            match = self._match_item(bib, item, trust_ref_doi=False)
            if match.status in {"validated", "invalid"} and (best is None or match.score > best.score):
                best = match
        return best or ResolutionMatch("crossref", "not_found", 0.0, "no usable results")

    def _match_item(self, bib: BibliographicData, item: dict, *, trust_ref_doi: bool) -> ResolutionMatch:
        if not _source_type_matches(bib.item_type, item.get("type")):
            status = "invalid" if trust_ref_doi else "not_found"
            return ResolutionMatch("crossref", status, 0.0, f"type mismatch ({item.get('type')})")
        title = item.get("title", [""])
        resolved_title = title[0] if isinstance(title, list) and title else title or ""
        container = item.get("container-title", [""])
        container_title = container[0] if isinstance(container, list) and container else container or ""
        author = (item.get("author") or [{}])[0]
        candidate_year = (
            str(((item.get("published") or {}).get("date-parts") or [[None]])[0][0] or "")
            or str(((item.get("created") or {}).get("date-parts") or [[None]])[0][0] or "")
        )
        first_author = author.get("family") or author.get("literal") or ""
        score, doi_mismatch, title_score, container_score = _score_candidate(
            bib,
            title=resolved_title,
            doi=item.get("DOI"),
            year=candidate_year,
            first_author_last_name=first_author,
            container_title=container_title,
            trust_ref_doi=trust_ref_doi,
        )

        if doi_mismatch:
            candidate = _make_bib(
                bib,
                title=resolved_title or None,
                creators=_crossref_people(item.get("author")) or None,
                editors=_crossref_people(item.get("editor")) or None,
                publication_title=container_title or None,
                year=candidate_year or None,
                volume=_clean(item.get("volume")) or None,
                issue=_clean(item.get("issue")) or None,
                pages=_normalize_pages(_clean(item.get("page"))) or None,
                doi=_clean(item.get("DOI")) or None,
                publisher=_clean(item.get("publisher")) or None,
                place=_clean(item.get("publisher-location")) or None,
                sparse=True,
            )
            return ResolutionMatch(
                "crossref",
                "invalid",
                0.0,
                f"DOI mismatch (got {item.get('DOI')})",
                candidate,
                title_score=title_score,
                container_score=container_score,
                year_match=bool(bib.year and candidate_year and bib.year == candidate_year),
            )
        if bib.title and trust_ref_doi and title_score < MIN_TITLE_SCORE_FOR_DOI_MATCH:
            candidate = _make_bib(
                bib,
                title=resolved_title or None,
                creators=_crossref_people(item.get("author")) or None,
                editors=_crossref_people(item.get("editor")) or None,
                publication_title=container_title or None,
                year=candidate_year or None,
                volume=_clean(item.get("volume")) or None,
                issue=_clean(item.get("issue")) or None,
                pages=_normalize_pages(_clean(item.get("page"))) or None,
                doi=_clean(item.get("DOI")) or None,
                publisher=_clean(item.get("publisher")) or None,
                place=_clean(item.get("publisher-location")) or None,
                sparse=True,
            )
            return ResolutionMatch(
                "crossref",
                "invalid",
                title_score,
                f"title mismatch (score {title_score:.2f})",
                candidate,
                title_score=title_score,
                container_score=container_score,
                year_match=bool(bib.year and candidate_year and bib.year == candidate_year),
            )
        if not trust_ref_doi and not _has_secondary_signal(
            bib,
            container_score=container_score,
            year=candidate_year,
            first_author_last_name=first_author,
        ):
            return ResolutionMatch(
                "crossref",
                "not_found",
                title_score,
                "no secondary validation signal",
                title_score=title_score,
                container_score=container_score,
                year_match=bool(bib.year and candidate_year and bib.year == candidate_year),
            )

        creators = _crossref_people(item.get("author"))
        editors = _crossref_people(item.get("editor"))
        isbn = item.get("ISBN")
        resolved_isbn = isbn[0] if isinstance(isbn, list) and isbn else isbn or None
        collection_title = item.get("collection-title")
        resolved_series = collection_title[0] if isinstance(collection_title, list) and collection_title else collection_title

        if score >= 0.8:
            candidate = _make_bib(
                bib,
                title=resolved_title,
                creators=creators or None,
                editors=editors or None,
                publication_title=container_title or None,
                year=candidate_year or None,
                volume=_clean(item.get("volume")) or None,
                issue=_clean(item.get("issue")) or None,
                pages=_clean(item.get("page")) or None,
                doi=item.get("DOI"),
                publisher=_clean(item.get("publisher")) or None,
                place=_clean(item.get("publisher-location")) or None,
                series=_clean(resolved_series) or None,
                edition=_clean(item.get("edition")) or _clean(item.get("edition-number")) or None,
                isbn=_clean(resolved_isbn) or None,
                sparse=True,
            )
            return ResolutionMatch(
                "crossref",
                "validated",
                score,
                f"matched (score {score:.2f})",
                candidate,
                title_score=title_score,
                container_score=container_score,
                year_match=bool(bib.year and candidate_year and bib.year == candidate_year),
            )

        candidate = _make_bib(
            bib,
            title=resolved_title or None,
            creators=_crossref_people(item.get("author")) or None,
            editors=_crossref_people(item.get("editor")) or None,
            publication_title=container_title or None,
            year=candidate_year or None,
            volume=_clean(item.get("volume")) or None,
            issue=_clean(item.get("issue")) or None,
            pages=_normalize_pages(_clean(item.get("page"))) or None,
            doi=_clean(item.get("DOI")) or None,
            publisher=_clean(item.get("publisher")) or None,
            place=_clean(item.get("publisher-location")) or None,
            series=resolved_series or None,
            sparse=True,
        )
        return ResolutionMatch(
            "crossref",
            "invalid",
            score,
            f"best score too low ({score:.2f})",
            candidate,
            title_score=title_score,
            container_score=container_score,
            year_match=bool(bib.year and candidate_year and bib.year == candidate_year),
        )


class OpenAlexResolver:
    def __init__(self) -> None:
        self.base = "https://api.openalex.org"
        self.mailto = settings.openalex_email

    def resolve(self, bib: BibliographicData) -> ResolutionMatch:
        doi = _normalize_doi(bib.doi)
        if doi and self.mailto:
            response = requests.get(
                f"{self.base}/works/doi:{doi}",
                params={"mailto": self.mailto},
                timeout=20,
            )
            if response.status_code == 404:
                return ResolutionMatch("openalex", "not_found", 0.0, "DOI not found")
            if not response.ok:
                return ResolutionMatch("openalex", "error", 0.0, f"request failed (HTTP {response.status_code})")
            return self._match_work(bib, response.json(), trust_ref_doi=True)

        if not self.mailto:
            return ResolutionMatch("openalex", "error", 0.0, "OpenAlex disabled")

        params = {"search": bib.title, "per-page": 5, "mailto": self.mailto}
        if bib.year:
            params["filter"] = f"from_publication_date:{bib.year}-01-01,to_publication_date:{bib.year}-12-31"
        response = requests.get(f"{self.base}/works", params=params, timeout=20)
        if not response.ok:
            return ResolutionMatch("openalex", "error", 0.0, f"search failed (HTTP {response.status_code})")
        results = response.json().get("results", [])
        if not results:
            return ResolutionMatch("openalex", "not_found", 0.0, "no results")

        best: ResolutionMatch | None = None
        for work in results:
            match = self._match_work(bib, work, trust_ref_doi=False)
            if match.status in {"validated", "invalid"} and (best is None or match.score > best.score):
                best = match
        return best or ResolutionMatch("openalex", "not_found", 0.0, "no usable results")

    def _match_work(self, bib: BibliographicData, work: dict, *, trust_ref_doi: bool) -> ResolutionMatch:
        source_type = work.get("type_crossref") or work.get("type")
        if not _source_type_matches(bib.item_type, source_type):
            status = "invalid" if trust_ref_doi else "not_found"
            return ResolutionMatch("openalex", status, 0.0, f"type mismatch ({source_type})")
        source = ((work.get("primary_location") or {}).get("source") or {})
        container_title = source.get("display_name") or ""
        title = work.get("display_name") or ""
        candidate_year = str(work.get("publication_year") or "")
        first_author_name = (((work.get("authorships") or [{}])[0].get("author") or {}).get("display_name") or "")
        first_author_last_name = first_author_name.split()[-1] if first_author_name else ""
        creators = []
        for authorship in work.get("authorships") or []:
            display_name = ((authorship.get("author") or {}).get("display_name") or "").strip()
            if display_name:
                parts = display_name.split()
                if len(parts) > 1:
                    creators.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
                else:
                    creators.append(display_name)
        score, doi_mismatch, title_score, container_score = _score_candidate(
            bib,
            title=title,
            doi=work.get("doi"),
            year=candidate_year,
            first_author_last_name=first_author_last_name,
            container_title=container_title,
            trust_ref_doi=trust_ref_doi,
        )

        if doi_mismatch:
            candidate = _make_bib(
                bib,
                title=title or None,
                creators=creators or None,
                publication_title=container_title or None,
                year=candidate_year or None,
                volume=_clean((work.get("biblio") or {}).get("volume")) or None,
                issue=_clean((work.get("biblio") or {}).get("issue")) or None,
                doi=work.get("doi"),
                sparse=True,
            )
            return ResolutionMatch(
                "openalex",
                "invalid",
                0.0,
                f"DOI mismatch (got {work.get('doi')})",
                candidate,
                title_score=title_score,
                container_score=container_score,
                year_match=bool(bib.year and candidate_year and bib.year == candidate_year),
            )
        if bib.title and trust_ref_doi and title_score < MIN_TITLE_SCORE_FOR_DOI_MATCH:
            candidate = _make_bib(
                bib,
                title=title or None,
                creators=creators or None,
                publication_title=container_title or None,
                year=candidate_year or None,
                volume=_clean((work.get("biblio") or {}).get("volume")) or None,
                issue=_clean((work.get("biblio") or {}).get("issue")) or None,
                doi=work.get("doi"),
                sparse=True,
            )
            return ResolutionMatch(
                "openalex",
                "invalid",
                title_score,
                f"title mismatch (score {title_score:.2f})",
                candidate,
                title_score=title_score,
                container_score=container_score,
                year_match=bool(bib.year and candidate_year and bib.year == candidate_year),
            )
        if not trust_ref_doi and not _has_secondary_signal(
            bib,
            container_score=container_score,
            year=candidate_year,
            first_author_last_name=first_author_last_name,
        ):
            return ResolutionMatch(
                "openalex",
                "not_found",
                title_score,
                "no secondary validation signal",
                title_score=title_score,
                container_score=container_score,
                year_match=bool(bib.year and candidate_year and bib.year == candidate_year),
            )

        if score >= 0.8:
            biblio = work.get("biblio") or {}
            first_page = _clean(biblio.get("first_page"))
            last_page = _clean(biblio.get("last_page"))
            pages = f"{first_page}-{last_page}" if first_page and last_page else first_page or bib.pages
            ids = work.get("ids") or {}
            candidate = _make_bib(
                bib,
                title=title,
                creators=creators or None,
                publication_title=container_title or None,
                year=candidate_year or None,
                volume=_clean(biblio.get("volume")) or None,
                issue=_clean(biblio.get("issue")) or None,
                pages=pages or None,
                doi=work.get("doi"),
                isbn=_clean(ids.get("isbn")) or None,
                sparse=True,
            )
            return ResolutionMatch(
                "openalex",
                "validated",
                score,
                f"matched (score {score:.2f})",
                candidate,
                title_score=title_score,
                container_score=container_score,
                year_match=bool(bib.year and candidate_year and bib.year == candidate_year),
            )

        candidate = _make_bib(
            bib,
            title=title or None,
            creators=creators or None,
            publication_title=container_title or None,
            year=candidate_year or None,
            volume=_clean((work.get("biblio") or {}).get("volume")) or None,
            issue=_clean((work.get("biblio") or {}).get("issue")) or None,
            pages=_normalize_pages(
                (
                    f"{_clean((work.get('biblio') or {}).get('first_page'))}-{_clean((work.get('biblio') or {}).get('last_page'))}"
                    if _clean((work.get("biblio") or {}).get("first_page")) and _clean((work.get("biblio") or {}).get("last_page"))
                    else _clean((work.get("biblio") or {}).get("first_page"))
                )
            )
            or None,
            doi=work.get("doi"),
            isbn=_clean((work.get("ids") or {}).get("isbn")) or None,
            sparse=True,
        )
        return ResolutionMatch(
            "openalex",
            "invalid",
            score,
            f"best score too low ({score:.2f})",
            candidate,
            title_score=title_score,
            container_score=container_score,
            year_match=bool(bib.year and candidate_year and bib.year == candidate_year),
        )


class LobidResolver:
    def resolve(self, bib: BibliographicData) -> ResolutionMatch:
        if bib.item_type not in {"bookSection", "book"}:
            return ResolutionMatch("lobid", "not_found", 0.0, "skipped for item type")
        query = _build_lobid_query(bib)
        if not query:
            return ResolutionMatch("lobid", "not_found", 0.0, "missing title")

        response = requests.get(
            "https://lobid.org/resources/search",
            params={"q": query, "size": 5, "format": "json"},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        if not response.ok:
            return ResolutionMatch("lobid", "error", 0.0, f"search failed (HTTP {response.status_code})")

        results = response.json().get("member", [])
        if not results:
            return ResolutionMatch("lobid", "not_found", 0.0, "no results")

        search_title = _search_title_for_catalog(bib)
        best: tuple[dict, float] | None = None
        for item in results:
            title_value = item.get("title")
            candidate_title = title_value[0] if isinstance(title_value, list) and title_value else title_value or ""
            candidate_year = ""
            publication = item.get("publication")
            nodes = publication if isinstance(publication, list) else [publication] if publication else []
            for node in nodes:
                candidate_year = _year_from_text((node or {}).get("startDate") or (node or {}).get("dateStatement"))
                if candidate_year:
                    break
            title_score = _dice_coefficient(search_title, candidate_title)
            year_score = 1.0 if bib.year and candidate_year and bib.year == candidate_year else 0.0
            combined = 0.9 * title_score + 0.1 * year_score
            if best is None or combined > best[1]:
                best = (item, combined)

        if not best:
            return ResolutionMatch("lobid", "not_found", 0.0, "no usable results")

        item, score = best
        if score < 0.8:
            return ResolutionMatch("lobid", "not_found", score, f"best score too low ({score:.2f})")

        title_value = item.get("title")
        candidate_title = title_value[0] if isinstance(title_value, list) and title_value else title_value or ""
        publication = item.get("publication")
        nodes = publication if isinstance(publication, list) else [publication] if publication else []
        candidate_year = ""
        publisher = None
        place = None
        for node in nodes:
            node = node or {}
            candidate_year = candidate_year or _year_from_text(_string_value(node.get("startDate") or node.get("dateStatement")))
            publisher = publisher or _string_value(node.get("publisher")) or None
            place = place or _string_value(node.get("place")) or None
        edition = _string_value(item.get("edition")) or None
        series = _string_value(item.get("series")) or None
        isbn = _extract_isbn_from_identifiers(item.get("identifiedBy") if isinstance(item.get("identifiedBy"), list) else [])

        if bib.item_type == "bookSection":
            candidate = _make_bib(
                bib,
                publication_title=candidate_title or None,
                year=candidate_year or None,
                publisher=publisher or None,
                place=place or None,
                series=series or None,
                edition=edition or None,
                isbn=isbn or None,
                sparse=True,
            )
        else:
            candidate = _make_bib(
                bib,
                title=candidate_title or None,
                year=candidate_year or None,
                publisher=publisher or None,
                place=place or None,
                series=series or None,
                edition=edition or None,
                isbn=isbn or None,
                sparse=True,
            )

        return ResolutionMatch(
            "lobid",
            "validated",
            score,
            f"matched (score {score:.2f})",
            candidate,
            title_score=_dice_coefficient(search_title, candidate_title),
            container_score=_dice_coefficient(bib.publication_title or "", candidate_title if bib.item_type == "bookSection" else ""),
            year_match=bool(bib.year and candidate_year and bib.year == candidate_year),
        )


class GbVResolver:
    def __init__(self) -> None:
        self.base_url = settings.gbv_sru_url.strip()

    def resolve(self, bib: BibliographicData) -> ResolutionMatch:
        if bib.item_type not in {"bookSection", "book"}:
            return ResolutionMatch("gbv", "not_found", 0.0, "skipped for item type")

        search_title = _search_title_for_catalog(bib)
        title_tokens = [_normalize_text(token) for token in search_title.split() if len(_normalize_text(token)) >= 3][:5]
        if not title_tokens:
            return ResolutionMatch("gbv", "not_found", 0.0, "missing title")

        title_query = " and ".join(f"pica.tit={token}" for token in title_tokens)
        author_last_name = _first_author_last_name(bib)
        author_query = f" and pica.per={author_last_name}" if author_last_name and bib.item_type != "bookSection" else ""
        query = f"{title_query}{author_query}"

        response = requests.get(
            self.base_url,
            params={
                "version": "1.1",
                "operation": "searchRetrieve",
                "query": query,
                "maximumRecords": "5",
                "recordSchema": "dc",
            },
            headers={"Accept": "application/xml"},
            timeout=30,
        )
        if not response.ok:
            return ResolutionMatch("gbv", "error", 0.0, f"search failed (HTTP {response.status_code})")
        if not response.text.strip():
            return ResolutionMatch("gbv", "error", 0.0, "empty SRU response")

        parsed = _parse_sru_dc_records(response.text)
        if not parsed:
            return ResolutionMatch("gbv", "not_found", 0.0, "no results")

        best: tuple[dict, float] | None = None
        for item in parsed:
            candidate_year = _year_from_text(item.get("date"))
            candidate_surname = item.get("creator", "").split(",")[0] or item.get("creator", "").split(" ")[-1]
            title_score = _dice_coefficient(search_title, item.get("title") or "")
            author_score = 1.0 if author_last_name and candidate_surname and author_last_name == _normalize_text(candidate_surname) else 0.0
            score = 0.9 * title_score + 0.1 * author_score
            if best is None or score > best[1]:
                best = (item, score)

        if not best:
            return ResolutionMatch("gbv", "not_found", 0.0, "no usable results")

        item, score = best
        if score < 0.8:
            return ResolutionMatch("gbv", "not_found", score, f"best score too low ({score:.2f})")

        candidate_year = _year_from_text(item.get("date"))
        isbn = _extract_isbn_from_identifiers(item.get("identifiers") or [])
        publisher = _clean(item.get("publisher")) or None

        if bib.item_type == "bookSection":
            candidate = _make_bib(
                bib,
                publication_title=_clean(item.get("title")) or None,
                year=candidate_year or None,
                publisher=publisher,
                isbn=isbn or None,
                sparse=True,
            )
        else:
            candidate = _make_bib(
                bib,
                title=_clean(item.get("title")) or None,
                year=candidate_year or None,
                publisher=publisher,
                isbn=isbn or None,
                sparse=True,
            )

        return ResolutionMatch(
            "gbv",
            "validated",
            score,
            f"matched (score {score:.2f})",
            candidate,
            title_score=_dice_coefficient(search_title, item.get("title") or ""),
            container_score=_dice_coefficient(bib.publication_title or "", item.get("title") or "") if bib.item_type == "bookSection" else 0.0,
            year_match=bool(bib.year and candidate_year and bib.year == candidate_year),
        )


class ResolutionService:
    def __init__(self) -> None:
        self.priorities = {
            "lobid": settings.resolution_priority_lobid,
            "gbv": settings.resolution_priority_gbv,
            "crossref": settings.resolution_priority_crossref,
            "openalex": settings.resolution_priority_openalex,
        }
        self.base_resolvers = [CrossrefResolver(), OpenAlexResolver()]
        self.book_resolvers = [LobidResolver(), GbVResolver()]

    def normalize(self, bib: BibliographicData) -> NormalizationResult:
        resolvers = list(self.base_resolvers)
        if bib.item_type in {"bookSection", "book"}:
            resolvers = [*self.book_resolvers, *resolvers]
        matches = [resolver.resolve(bib) for resolver in resolvers]
        visible_matches = [match for match in matches if not _should_hide_evidence_match(bib, match)]
        evidence = [
            ResolutionEvidence(
                source=match.source,
                status=match.status,
                score=match.score,
                explanation=match.explanation,
                candidate_json=match.candidate.model_dump_json() if match.candidate else None,
            )
            for match in visible_matches
        ]
        validated = [match for match in matches if match.status == "validated" and match.candidate is not None]
        if validated:
            validated.sort(key=lambda match: _validation_sort_key(self.priorities, match))
            best = validated[0]
            overwrite = best.score >= 0.95
            overwrite_authors = _allow_author_overwrite(bib, best)
            normalized_bib = _apply_candidate(
                bib,
                best.candidate,
                overwrite=overwrite,
                overwrite_authors=overwrite_authors,
            )
            for match in validated[1:]:
                if not match.candidate:
                    continue
                normalized_bib = _apply_candidate(
                    normalized_bib,
                    match.candidate,
                    overwrite=False,
                    overwrite_authors=_allow_author_overwrite(normalized_bib, match),
                )
            author_match = _best_author_enrichment_match(bib, matches)
            if (
                author_match
                and author_match.candidate
                and author_match.candidate.creators
                and author_match.candidate.creators != normalized_bib.creators
            ):
                normalized_bib = _apply_candidate(
                    normalized_bib,
                    _make_bib(normalized_bib, creators=author_match.candidate.creators, sparse=True),
                    overwrite=False,
                    overwrite_authors=True,
                )
                note_suffix = f"authors enriched from {author_match.source}"
            else:
                note_suffix = None
            return NormalizationResult(
                bibliographic_data=normalized_bib,
                source=best.source,
                confidence=best.score,
                notes=self._summarize(visible_matches, best.source, note_suffix),
                evidence=evidence,
            )

        fallback_confidence = max((match.score for match in matches), default=0.0)
        return NormalizationResult(
            bibliographic_data=bib,
            source="original",
            confidence=fallback_confidence,
            notes=self._summarize(visible_matches, None),
            evidence=evidence,
        )

    def _summarize(self, matches: list[ResolutionMatch], winner: str | None, suffix: str | None = None) -> str:
        parts = []
        for match in matches:
            prefix = f"{match.source}* " if winner and match.source == winner else f"{match.source} "
            parts.append(f"{prefix}{match.status}: {match.explanation}")
        if suffix:
            parts.append(suffix)
        return "; ".join(parts)
