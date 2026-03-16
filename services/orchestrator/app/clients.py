from __future__ import annotations

import html
import hashlib
import re
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

import requests
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.models import EmailTemplate
from app.schemas import (
    BibliographicData,
    ClarificationNotificationPayload,
    DeliveryNotificationPayload,
    NormalizationResult,
)
from app.templates import DEFAULT_CLARIFICATION_TEMPLATES, DEFAULT_EMAIL_TEMPLATES, render_template


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _normalize_text(value: str | None) -> str:
    return (
        _clean(value)
        .casefold()
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


def _dice_coefficient(left: str | None, right: str | None) -> float:
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


def _extract_year(raw: str | None) -> str:
    match = re.search(r"\b(1[5-9]\d{2}|20\d{2}|21\d{2})\b", raw or "")
    return match.group(1) if match else ""


def _creator_last_name(value: str | None) -> str:
    cleaned = _clean(value)
    if not cleaned:
        return ""
    if "," in cleaned:
        return _normalize_text(cleaned.split(",", 1)[0])
    return _normalize_text(cleaned.split()[-1])


def _normalize_language(value: str | None) -> str:
    normalized = _clean(value).lower()
    if normalized.startswith("de"):
        return "de"
    if normalized.startswith("pl"):
        return "pl"
    if normalized.startswith("en"):
        return "en"
    return "de"


def _strip_html(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class OpenAlexClient:
    def __init__(self) -> None:
        self.base = "https://api.openalex.org"
        self.mailto = settings.openalex_email

    def normalize(self, bib: BibliographicData) -> NormalizationResult:
        if not self.mailto:
            return NormalizationResult(
                bibliographic_data=bib,
                source="original",
                confidence=0.0,
                notes="OpenAlex disabled",
            )

        params = {"mailto": self.mailto, "per-page": 1}
        if bib.doi:
            response = requests.get(
                f"{self.base}/works/https://doi.org/{bib.doi}",
                params={"mailto": self.mailto},
                timeout=20,
            )
            if response.ok:
                return NormalizationResult(
                    bibliographic_data=self._work_to_bib(response.json(), fallback=bib),
                    source="openalex",
                    confidence=1.0,
                    notes="Exact DOI match",
                )

        query = " ".join(part for part in [bib.title, bib.publication_title, bib.year] if part).strip()
        if not query:
            return NormalizationResult(
                bibliographic_data=bib,
                source="original",
                confidence=0.0,
                notes="No lookup query available",
            )

        response = requests.get(
            f"{self.base}/works",
            params={**params, "search": query},
            timeout=20,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return NormalizationResult(
                bibliographic_data=bib,
                source="original",
                confidence=0.0,
                notes="No OpenAlex candidate found",
            )
        work = results[0]
        normalized = self._work_to_bib(work, fallback=bib)
        confidence = self._score_candidate(bib, normalized)
        return NormalizationResult(
            bibliographic_data=normalized,
            source="openalex",
            confidence=confidence,
            notes="Search match",
        )

    def _work_to_bib(self, work: dict, fallback: BibliographicData) -> BibliographicData:
        location = work.get("primary_location") or {}
        source = location.get("source") or {}
        creators = [author["author"]["display_name"] for author in work.get("authorships", []) if author.get("author")]
        year = str(work.get("publication_year") or fallback.year)
        return BibliographicData(
            item_type=fallback.item_type,
            title=work.get("display_name") or fallback.title,
            creators=creators or fallback.creators,
            publication_title=source.get("display_name") or fallback.publication_title,
            year=year,
            volume=_clean(work.get("biblio", {}).get("volume")) or fallback.volume,
            issue=_clean(work.get("biblio", {}).get("issue")) or fallback.issue,
            pages=self._pages_from_biblio(work.get("biblio", {}), fallback.pages),
            doi=(work.get("doi") or "").replace("https://doi.org/", "") or fallback.doi,
            language=fallback.language,
            abstract_note=fallback.abstract_note,
        )

    def _pages_from_biblio(self, biblio: dict, fallback: str | None) -> str | None:
        first_page = _clean(biblio.get("first_page"))
        last_page = _clean(biblio.get("last_page"))
        if first_page and last_page:
            return f"{first_page}-{last_page}"
        return first_page or fallback

    def _score_candidate(self, original: BibliographicData, normalized: BibliographicData) -> float:
        score = 0.0
        if original.doi and normalized.doi and original.doi.casefold().strip() == normalized.doi.casefold().strip():
            return 1.0
        if original.title.casefold().strip() == normalized.title.casefold().strip():
            score += 0.6
        if (
            original.publication_title.casefold().strip()
            and original.publication_title.casefold().strip() == normalized.publication_title.casefold().strip()
        ):
            score += 0.2
        if original.year and original.year == normalized.year:
            score += 0.2
        return score


class NextcloudClient:
    def __init__(self) -> None:
        self.base = settings.nextcloud_base_url.rstrip("/")
        self.username = settings.nextcloud_username
        self.password = settings.nextcloud_password
        self.dav_base_path = settings.nextcloud_dav_base_path
        self.root = settings.nextcloud_root_path.rstrip("/")

    def upload_pdf(self, local_path: Path, remote_filename: str) -> str:
        remote_path = f"{self.root}/{remote_filename}".replace("//", "/")
        quoted_path = quote(remote_path)
        encoded_username = quote(self.username, safe="")
        dav_base_path = self.dav_base_path.replace("{username}", encoded_username).strip()
        if not dav_base_path.startswith("/"):
            dav_base_path = f"/{dav_base_path}"
        dav_base_path = dav_base_path.rstrip("/")
        upload_url = f"{self.base}{dav_base_path}{quoted_path}"

        with local_path.open("rb") as handle:
            response = requests.put(
                upload_url,
                data=handle,
                auth=(self.username, self.password),
                timeout=120,
            )

        response.raise_for_status()
        return remote_path

    def create_share_link(self, remote_path: str, expires_at: datetime) -> tuple[str, str]:
        share_url = f"{self.base}/ocs/v2.php/apps/files_sharing/api/v1/shares?format=json"
        response = requests.post(
            share_url,
            auth=(self.username, self.password),
            headers={"OCS-APIRequest": "true"},
            data={
                "path": remote_path,
                "shareType": 3,
                "permissions": 1,
                "expireDate": expires_at.strftime("%Y-%m-%d"),
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        public_url = payload["ocs"]["data"]["url"]
        return public_url, expires_at.strftime("%Y-%m-%d")


class ZoteroClient:
    def __init__(self) -> None:
        library_type = settings.zotero_library_type.strip().lower()
        if library_type not in {"user", "group"}:
            raise ValueError("ZOTERO_LIBRARY_TYPE must be 'user' or 'group'.")
        scope = "users" if library_type == "user" else "groups"
        self.base = f"https://api.zotero.org/{scope}/{settings.zotero_library_id}"
        self.api_key = settings.zotero_api_key
        self.collection_key = settings.zotero_collection_key
        self.in_process_tag = settings.zotero_in_process_tag

    def find_existing_item(self, bib: BibliographicData) -> dict | None:
        candidates: dict[str, dict] = {}
        for query in self._candidate_queries(bib):
            for item in self._search_items(query):
                key = item.get("key")
                if key:
                    candidates[key] = item

        best_match: dict | None = None
        best_score = 0.0
        for item in candidates.values():
            data = item.get("data", {})
            if data.get("itemType") == "attachment":
                continue
            match = self._score_existing_item(bib, item)
            if not match:
                continue
            if match["score"] > best_score:
                best_score = match["score"]
                best_match = match
        return best_match

    def create_item(self, bib: BibliographicData, request_id: str) -> str:
        endpoint = f"{self.base}/items"
        item = self._item_payload(bib, request_id)
        response = requests.post(
            endpoint,
            headers=self._headers(content_type="application/json"),
            params={"key": self.api_key},
            json=[item],
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        successful = payload.get("successful", {})
        if not successful:
            raise RuntimeError(f"No successful Zotero create response: {payload}")
        first_key = next(iter(successful))
        return successful[first_key]["key"]

    def find_pdf_attachment(self, item_key: str) -> str | None:
        response = requests.get(
            f"{self.base}/items/{item_key}/children",
            headers=self._headers(),
            params={"key": self.api_key},
            timeout=30,
        )
        response.raise_for_status()
        for child in response.json():
            data = child.get("data", {})
            if data.get("itemType") == "attachment" and data.get("contentType") == "application/pdf":
                return child.get("key")
        return None

    def download_attachment(self, attachment_key: str, destination: Path) -> Path:
        response = requests.get(
            f"{self.base}/items/{attachment_key}/file",
            headers=self._headers(),
            params={"key": self.api_key},
            stream=True,
            timeout=120,
        )
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 64):
                if chunk:
                    handle.write(chunk)
        return destination

    def upload_pdf_attachment(self, parent_item_key: str, source_pdf: Path, title: str | None = None) -> str:
        attachment_key = self._create_attachment_item(parent_item_key, source_pdf.name, title=title)
        upload_response = self._authorize_attachment_upload(attachment_key, source_pdf)
        if upload_response.status_code == 204:
            return attachment_key
        upload_data = upload_response.json() if upload_response.content else {}
        if upload_data.get("exists") in {1, "1", True}:
            return attachment_key
        upload_url = upload_data.get("url")
        upload_key = upload_data.get("uploadKey")
        if not upload_url or not upload_key:
            raise RuntimeError(f"Unexpected Zotero upload authorization response for {attachment_key}: {upload_data}")

        prefix = upload_data.get("prefix", "")
        suffix = upload_data.get("suffix", "")
        content_type = upload_data.get("contentType") or "application/octet-stream"
        payload = prefix.encode("utf-8") + source_pdf.read_bytes() + suffix.encode("utf-8")
        upload_result = requests.post(
            upload_url,
            data=payload,
            headers={"Content-Type": content_type},
            timeout=300,
        )
        upload_result.raise_for_status()

        finalize = requests.post(
            f"{self.base}/items/{attachment_key}/file",
            headers=self._headers(
                content_type="application/x-www-form-urlencoded",
                extra_headers={"If-None-Match": "*"},
            ),
            params={"key": self.api_key},
            data={"upload": upload_key},
            timeout=60,
        )
        finalize.raise_for_status()
        return attachment_key

    def render_bibliography_item(self, item_key: str, style: str, locale: str) -> tuple[str, str]:
        response = requests.get(
            f"{self.base}/items/{item_key}",
            headers=self._headers(),
            params={
                "key": self.api_key,
                "format": "bib",
                "style": style,
                "locale": locale,
                "linkwrap": 1,
            },
            timeout=30,
        )
        response.raise_for_status()
        bibliography_html = response.text.strip()
        if not bibliography_html:
            raise RuntimeError(f"Zotero bibliography rendering returned an empty response for item {item_key}.")
        return bibliography_html, _strip_html(bibliography_html)

    def _create_attachment_item(self, parent_item_key: str, filename: str, title: str | None = None) -> str:
        endpoint = f"{self.base}/items"
        item = {
            "itemType": "attachment",
            "parentItem": parent_item_key,
            "linkMode": "imported_file",
            "title": title or filename,
            "filename": filename,
            "contentType": "application/pdf",
            "tags": [{"tag": self.in_process_tag}],
        }
        response = requests.post(
            endpoint,
            headers=self._headers(content_type="application/json"),
            params={"key": self.api_key},
            json=[item],
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        successful = payload.get("successful", {})
        if not successful:
            raise RuntimeError(f"No successful Zotero attachment create response: {payload}")
        first_key = next(iter(successful))
        return successful[first_key]["key"]

    def _authorize_attachment_upload(self, attachment_key: str, source_pdf: Path) -> requests.Response:
        payload = {
            "md5": hashlib.md5(source_pdf.read_bytes()).hexdigest(),
            "filename": source_pdf.name,
            "filesize": str(source_pdf.stat().st_size),
            "mtime": str(int(source_pdf.stat().st_mtime * 1000)),
        }
        response = requests.post(
            f"{self.base}/items/{attachment_key}/file",
            headers=self._headers(
                content_type="application/x-www-form-urlencoded",
                extra_headers={"If-None-Match": "*"},
            ),
            params={"key": self.api_key},
            data=payload,
            timeout=60,
        )
        response.raise_for_status()
        return response

    def _search_items(self, query: str) -> list[dict]:
        if not query:
            return []
        if self.collection_key:
            url = f"{self.base}/collections/{self.collection_key}/items/top"
        else:
            url = f"{self.base}/items/top"
        response = requests.get(
            url,
            headers=self._headers(),
            params={"key": self.api_key, "q": query},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def _candidate_queries(self, bib: BibliographicData) -> list[str]:
        first_author = bib.creators[0] if bib.creators else ""
        queries = [
            _normalize_doi(bib.doi),
            bib.title,
            bib.publication_title,
            " ".join(part for part in [bib.title, first_author] if part).strip(),
            " ".join(part for part in [first_author, bib.year] if part).strip(),
        ]
        seen: set[str] = set()
        result: list[str] = []
        for query in queries:
            cleaned = _clean(query)
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                result.append(cleaned)
        return result

    def _score_existing_item(self, bib: BibliographicData, item: dict) -> dict | None:
        data = item.get("data", {})
        item_type = self._normalize_item_type(data.get("itemType"))
        expected_type = self._normalize_item_type(bib.item_type)
        if item_type != expected_type:
            return None

        normalized_doi = _normalize_doi(bib.doi)
        candidate_doi = _normalize_doi(data.get("DOI"))
        if normalized_doi and candidate_doi and normalized_doi == candidate_doi:
            return {
                "key": item.get("key"),
                "data": data,
                "score": 1.0,
                "reason": "exact_doi",
            }

        title_score = _dice_coefficient(bib.title, data.get("title"))
        container_score = _dice_coefficient(bib.publication_title, self._container_title_from_item(data))
        author_score = self._author_score(bib, data)
        year_score = 1.0 if bib.year and _extract_year(data.get("date")) == bib.year else 0.0

        weighted_parts: list[tuple[float, float]] = []
        if bib.title:
            weighted_parts.append((title_score, 0.6))
        if bib.publication_title:
            weighted_parts.append((container_score, 0.2))
        if bib.creators:
            weighted_parts.append((author_score, 0.15))
        if bib.year:
            weighted_parts.append((year_score, 0.05))
        if not weighted_parts:
            return None

        total_weight = sum(weight for _, weight in weighted_parts)
        score = sum(part * weight for part, weight in weighted_parts) / total_weight
        exact_title = _normalize_text(bib.title) == _normalize_text(data.get("title"))
        if exact_title and (container_score >= 0.75 or author_score >= 1.0 or year_score >= 1.0):
            reason = "exact_title_plus_secondary"
        elif score >= 0.9:
            reason = "high_similarity"
        elif score >= 0.82 and title_score >= 0.92 and (container_score >= 0.6 or author_score >= 1.0 or year_score >= 1.0):
            reason = "strong_bibliographic_match"
        else:
            return None

        return {
            "key": item.get("key"),
            "data": data,
            "score": round(score, 4),
            "reason": reason,
        }

    def _author_score(self, bib: BibliographicData, data: dict) -> float:
        if not bib.creators:
            return 0.0
        target_last_name = _creator_last_name(bib.creators[0])
        if not target_last_name:
            return 0.0
        for creator in data.get("creators") or []:
            candidate = _clean(creator.get("lastName")) or _clean(creator.get("name"))
            if candidate and _creator_last_name(candidate) == target_last_name:
                return 1.0
        return 0.0

    def _container_title_from_item(self, data: dict) -> str:
        return _clean(data.get("publicationTitle")) or _clean(data.get("bookTitle"))

    def _item_payload(self, bib: BibliographicData, request_id: str) -> dict:
        item_type = self._normalize_item_type(bib.item_type)
        creators = [{"creatorType": "author", "name": creator} for creator in bib.creators]
        creators.extend({"creatorType": "editor", "name": editor} for editor in bib.editors)
        extra_lines = [f"Request-ID: {request_id}"]
        item = {
            "itemType": item_type,
            "title": bib.title,
            "creators": creators,
            "date": bib.year,
            "DOI": bib.doi or "",
            "language": bib.language or "",
            "abstractNote": bib.abstract_note or "",
            "tags": [{"tag": self.in_process_tag}],
            "extra": "",
        }
        if item_type == "journalArticle":
            item["publicationTitle"] = bib.publication_title
            item["volume"] = bib.volume or ""
            item["issue"] = bib.issue or ""
            item["pages"] = bib.pages or ""
        elif item_type == "bookSection":
            item["bookTitle"] = bib.publication_title
            item["pages"] = bib.pages or ""
            item["publisher"] = bib.publisher or ""
            item["place"] = bib.place or ""
            item["ISBN"] = bib.isbn or ""
            if bib.volume:
                extra_lines.append(f"Container-Volume: {bib.volume}")
            if bib.series:
                extra_lines.append(f"Series: {bib.series}")
            if bib.edition:
                extra_lines.append(f"Edition: {bib.edition}")
        else:
            item["publicationTitle"] = bib.publication_title
            item["volume"] = bib.volume or ""
            item["issue"] = bib.issue or ""
            item["pages"] = bib.pages or ""
        if item_type in {"book", "bookSection", "conferencePaper", "report"}:
            if bib.publisher:
                item["publisher"] = bib.publisher
            if bib.place:
                item["place"] = bib.place
        if item_type == "book":
            item["edition"] = bib.edition or ""
            item["ISBN"] = bib.isbn or ""
            item["series"] = bib.series or ""
        item["extra"] = "\n".join(extra_lines)
        if self.collection_key:
            item["collections"] = [self.collection_key]
        return item

    def _normalize_item_type(self, raw: str | None) -> str:
        value = (raw or "").strip()
        if not value:
            return "document"
        key = value.casefold().replace(" ", "")
        aliases = {
            "article": "journalArticle",
            "journalarticle": "journalArticle",
            "journal": "journalArticle",
            "booksection": "bookSection",
            "bookchapter": "bookSection",
            "chapter": "bookSection",
            "incollection": "bookSection",
            "conferencepaper": "conferencePaper",
            "inproceedings": "conferencePaper",
            "conference": "conferencePaper",
            "proceedings": "conferencePaper",
        }
        return aliases.get(key, value)

    def _headers(
        self,
        content_type: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        headers = {"Zotero-API-Version": "3"}
        if content_type:
            headers["Content-Type"] = content_type
        if extra_headers:
            headers.update(extra_headers)
        return headers


class NotificationClient:
    def __init__(self) -> None:
        self.smtp_host = settings.smtp_host
        self.smtp_port = settings.smtp_port
        self.smtp_username = settings.smtp_username
        self.smtp_password = settings.smtp_password
        self.smtp_use_tls = settings.smtp_use_tls
        self.smtp_use_ssl = settings.smtp_use_ssl
        self.smtp_from_email = settings.smtp_from_email
        self.smtp_from_name = settings.smtp_from_name
        self.smtp_reply_to = settings.smtp_reply_to
        self.citation_style = settings.citation_style
        self.citation_locales = {
            "de": settings.citation_locale_de,
            "en": settings.citation_locale_en,
            "pl": settings.citation_locale_pl,
        }

    def send_delivery(self, payload: DeliveryNotificationPayload) -> None:
        if not self.smtp_host:
            raise RuntimeError("SMTP_HOST must be configured for delivery notifications.")
        rendered_payload = self._with_rendered_citations(payload)
        language = _normalize_language(rendered_payload.language)
        template = self._template_for(language)
        context = self._template_context(rendered_payload)
        self._send_email(
            recipient=rendered_payload.user_email,
            subject=render_template(template["subject_template"], context),
            body_text=render_template(template["body_text_template"], context),
            body_html=render_template(template["body_html_template"], context),
        )

    def send_clarification_request(self, payload: ClarificationNotificationPayload) -> None:
        if not self.smtp_host:
            raise RuntimeError("SMTP_HOST must be configured for clarification notifications.")
        language = _normalize_language(payload.language)
        template = DEFAULT_CLARIFICATION_TEMPLATES.get(language, DEFAULT_CLARIFICATION_TEMPLATES["de"])
        context = self._clarification_template_context(payload)
        self._send_email(
            recipient=payload.user_email,
            subject=render_template(template["subject_template"], context),
            body_text=render_template(template["body_text_template"], context),
            body_html=render_template(template["body_html_template"], context),
        )

    def _send_email(self, recipient: str, subject: str, body_text: str, body_html: str) -> None:
        if not self.smtp_from_email:
            raise RuntimeError("SMTP_FROM_EMAIL must be configured when SMTP_HOST is set.")

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self._format_from_header()
        message["To"] = recipient
        if self.smtp_reply_to:
            message["Reply-To"] = self.smtp_reply_to
        message.set_content(body_text)
        message.add_alternative(body_html, subtype="html")

        if self.smtp_use_ssl:
            smtp = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30)
        else:
            smtp = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)

        with smtp:
            smtp.ehlo()
            if self.smtp_use_tls and not self.smtp_use_ssl:
                smtp.starttls()
                smtp.ehlo()
            if self.smtp_username:
                smtp.login(self.smtp_username, self.smtp_password or "")
            smtp.send_message(message)

    def _format_from_header(self) -> str:
        if self.smtp_from_name:
            return f"{self.smtp_from_name} <{self.smtp_from_email}>"
        return self.smtp_from_email or ""

    def _template_for(self, language: str) -> dict[str, str]:
        with session_scope() as session:
            template = session.scalar(select(EmailTemplate).where(EmailTemplate.language == language))
        if template:
            return {
                "subject_template": template.subject_template,
                "body_text_template": template.body_text_template,
                "body_html_template": template.body_html_template,
            }
        return DEFAULT_EMAIL_TEMPLATES.get(language, DEFAULT_EMAIL_TEMPLATES["de"])

    def _template_context(self, payload: DeliveryNotificationPayload) -> dict[str, str]:
        language = _normalize_language(payload.language)
        greeting_name = payload.user_name or self._translation(language, "greeting_fallback")
        return {
            "request_id": payload.request_id,
            "submission_id": payload.formcycle_submission_id or "",
            "user_email": payload.user_email,
            "user_name": payload.user_name or "",
            "greeting_name": greeting_name,
            "item_count": str(len(payload.items)),
            "items_html": self._items_html(payload),
            "items_text": self._items_text(payload),
            "sender_name": self.smtp_from_name,
        }

    def _clarification_template_context(self, payload: ClarificationNotificationPayload) -> dict[str, str]:
        language = _normalize_language(payload.language)
        greeting_name = payload.user_name or self._translation(language, "greeting_fallback")
        operator_message = (payload.operator_message or "").strip()
        return {
            "request_id": payload.request_id,
            "submission_id": payload.formcycle_submission_id or "",
            "user_email": payload.user_email,
            "user_name": payload.user_name or "",
            "greeting_name": greeting_name,
            "item_id": str(payload.item_id),
            "operator_message": operator_message,
            "operator_message_html": self._escape_html(operator_message).replace("\n", "<br>"),
            "clarification_url": payload.clarification_url,
            "sender_name": self.smtp_from_name,
        }

    def _items_html(self, payload: DeliveryNotificationPayload) -> str:
        language = _normalize_language(payload.language)
        item_blocks = []
        for item in payload.items:
            item_blocks.append(
                (
                    '<div style="margin: 0 0 1.25rem 0;">'
                    f"<strong>{self._translation(language, 'item_label')} {item.item_index + 1}</strong><br>"
                    f'<div style="margin: 0.4rem 0;">{item.citation_text}</div>'
                    f'<a href="{self._escape_html(item.download_url)}">{self._translation(language, "download_link")}</a><br>'
                    f"{self._translation(language, 'valid_until')}: {self._escape_html(item.expires_on)}"
                    "</div>"
                )
            )
        return "".join(item_blocks)

    def _items_text(self, payload: DeliveryNotificationPayload) -> str:
        language = _normalize_language(payload.language)
        blocks = []
        for item in payload.items:
            blocks.append(
                "\n".join(
                    [
                        f"{self._translation(language, 'item_label')} {item.item_index + 1}",
                        _strip_html(item.citation_text),
                        f"{self._translation(language, 'download_label')}: {item.download_url}",
                        f"{self._translation(language, 'valid_until')}: {item.expires_on}",
                    ]
                )
            )
        return "\n\n".join(blocks)

    def _with_rendered_citations(self, payload: DeliveryNotificationPayload) -> DeliveryNotificationPayload:
        language = _normalize_language(payload.language)
        locale = self.citation_locales.get(language, self.citation_locales["de"])
        zotero = ZoteroClient()
        rendered_items = []
        for item in payload.items:
            bibliography_html, bibliography_text = zotero.render_bibliography_item(
                item.zotero_item_key,
                style=self.citation_style,
                locale=locale,
            )
            rendered_items.append(
                item.model_copy(
                    update={
                        "citation_text": bibliography_html if bibliography_html else bibliography_text,
                    }
                )
            )
        return payload.model_copy(update={"items": rendered_items, "language": language})

    def _translation(self, language: str, key: str) -> str:
        translations = {
            "de": {
                "greeting": "Guten Tag",
                "greeting_fallback": "Guten Tag",
                "delivery_ready_html": "die angeforderte Dokumentlieferung ist bereit.",
                "delivery_ready_text": "die angeforderte Dokumentlieferung ist bereit.",
                "item_label": "Titel",
                "download_link": "PDF herunterladen",
                "download_label": "Download",
                "valid_until": "Link gueltig bis",
                "closing_html": "Mit freundlichen Gruessen",
                "closing_text": "Mit freundlichen Gruessen",
            },
            "en": {
                "greeting": "Hello",
                "greeting_fallback": "there",
                "delivery_ready_html": "your requested document delivery is ready.",
                "delivery_ready_text": "your requested document delivery is ready.",
                "item_label": "Item",
                "download_link": "Download PDF",
                "download_label": "Download",
                "valid_until": "Link valid until",
                "closing_html": "Kind regards",
                "closing_text": "Kind regards",
            },
            "pl": {
                "greeting": "Dzien dobry",
                "greeting_fallback": "Dzien dobry",
                "delivery_ready_html": "zamowione materialy sa gotowe do pobrania.",
                "delivery_ready_text": "zamowione materialy sa gotowe do pobrania.",
                "item_label": "Pozycja",
                "download_link": "Pobierz PDF",
                "download_label": "Pobieranie",
                "valid_until": "Link wazny do",
                "closing_html": "Z powazaniem",
                "closing_text": "Z powazaniem",
            },
        }
        return translations.get(language, translations["de"])[key]

    def _escape_html(self, value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
