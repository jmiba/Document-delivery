from __future__ import annotations

from dataclasses import dataclass

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
    publication_title: str | None = None,
    year: str | None = None,
    volume: str | None = None,
    issue: str | None = None,
    pages: str | None = None,
    doi: str | None = None,
    abstract_note: str | None = None,
) -> BibliographicData:
    item_type = fallback.item_type
    resolved_issue = issue if item_type == "journalArticle" else None
    return BibliographicData(
        item_type=item_type,
        title=title or fallback.title,
        creators=creators or fallback.creators,
        publication_title=publication_title or fallback.publication_title,
        year=year or fallback.year,
        volume=volume or fallback.volume,
        issue=resolved_issue if resolved_issue is not None else fallback.issue if item_type == "journalArticle" else None,
        pages=pages or fallback.pages,
        doi=_normalize_doi(doi) or fallback.doi,
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
            return ResolutionMatch("crossref", "invalid", 0.0, f"type mismatch ({item.get('type')})")
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
            return ResolutionMatch(
                "crossref",
                "invalid",
                0.0,
                f"DOI mismatch (got {item.get('DOI')})",
                title_score=title_score,
                container_score=container_score,
                year_match=bool(bib.year and candidate_year and bib.year == candidate_year),
            )
        if bib.title and trust_ref_doi and title_score < MIN_TITLE_SCORE_FOR_DOI_MATCH:
            return ResolutionMatch(
                "crossref",
                "invalid",
                title_score,
                f"title mismatch (score {title_score:.2f})",
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

        creators = []
        for creator in item.get("author") or []:
            given = _clean(creator.get("given"))
            family = _clean(creator.get("family"))
            literal = _clean(creator.get("literal"))
            if family and given:
                creators.append(f"{family}, {given}")
            elif family:
                creators.append(family)
            elif literal:
                creators.append(literal)

        if score >= 0.8:
            candidate = _make_bib(
                bib,
                title=resolved_title,
                creators=creators or None,
                publication_title=container_title or None,
                year=candidate_year or None,
                volume=_clean(item.get("volume")) or None,
                issue=_clean(item.get("issue")) or None,
                pages=_clean(item.get("page")) or None,
                doi=item.get("DOI"),
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

        return ResolutionMatch(
            "crossref",
            "invalid",
            score,
            f"best score too low ({score:.2f})",
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
            return ResolutionMatch("openalex", "invalid", 0.0, f"type mismatch ({source_type})")
        source = ((work.get("primary_location") or {}).get("source") or {})
        container_title = source.get("display_name") or ""
        title = work.get("display_name") or ""
        candidate_year = str(work.get("publication_year") or "")
        first_author_name = (((work.get("authorships") or [{}])[0].get("author") or {}).get("display_name") or "")
        first_author_last_name = first_author_name.split()[-1] if first_author_name else ""
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
            return ResolutionMatch(
                "openalex",
                "invalid",
                0.0,
                f"DOI mismatch (got {work.get('doi')})",
                title_score=title_score,
                container_score=container_score,
                year_match=bool(bib.year and candidate_year and bib.year == candidate_year),
            )
        if bib.title and trust_ref_doi and title_score < MIN_TITLE_SCORE_FOR_DOI_MATCH:
            return ResolutionMatch(
                "openalex",
                "invalid",
                title_score,
                f"title mismatch (score {title_score:.2f})",
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

        creators = []
        for authorship in work.get("authorships") or []:
            display_name = ((authorship.get("author") or {}).get("display_name") or "").strip()
            if display_name:
                parts = display_name.split()
                if len(parts) > 1:
                    creators.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
                else:
                    creators.append(display_name)

        if score >= 0.8:
            biblio = work.get("biblio") or {}
            first_page = _clean(biblio.get("first_page"))
            last_page = _clean(biblio.get("last_page"))
            pages = f"{first_page}-{last_page}" if first_page and last_page else first_page or bib.pages
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

        return ResolutionMatch(
            "openalex",
            "invalid",
            score,
            f"best score too low ({score:.2f})",
            title_score=title_score,
            container_score=container_score,
            year_match=bool(bib.year and candidate_year and bib.year == candidate_year),
        )


class ResolutionService:
    def __init__(self) -> None:
        self.priorities = {
            "crossref": settings.resolution_priority_crossref,
            "openalex": settings.resolution_priority_openalex,
        }
        self.resolvers = [CrossrefResolver(), OpenAlexResolver()]

    def normalize(self, bib: BibliographicData) -> NormalizationResult:
        matches = [resolver.resolve(bib) for resolver in self.resolvers]
        evidence = [
            ResolutionEvidence(
                source=match.source,
                status=match.status,
                score=match.score,
                explanation=match.explanation,
                candidate_json=match.candidate.model_dump_json() if match.candidate else None,
            )
            for match in matches
        ]
        validated = [match for match in matches if match.status == "validated" and match.candidate is not None]
        if validated:
            validated.sort(key=lambda match: (self.priorities.get(match.source, 100), -match.score))
            best = validated[0]
            overwrite = best.score >= 0.95
            overwrite_authors = _allow_author_overwrite(bib, best)
            normalized_bib = _apply_candidate(
                bib,
                best.candidate,
                overwrite=overwrite,
                overwrite_authors=overwrite_authors,
            )
            return NormalizationResult(
                bibliographic_data=normalized_bib,
                source=best.source,
                confidence=best.score,
                notes=self._summarize(matches, best.source),
                evidence=evidence,
            )

        fallback_confidence = max((match.score for match in matches), default=0.0)
        return NormalizationResult(
            bibliographic_data=bib,
            source="original",
            confidence=fallback_confidence,
            notes=self._summarize(matches, None),
            evidence=evidence,
        )

    def _summarize(self, matches: list[ResolutionMatch], winner: str | None) -> str:
        parts = []
        for match in matches:
            prefix = f"{match.source}* " if winner and match.source == winner else f"{match.source} "
            parts.append(f"{prefix}{match.status}: {match.explanation}")
        return "; ".join(parts)
