from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

from app.config import settings
from app.schemas import BibliographicData, DeliveryNotificationPayload


def _clean(value: str | None) -> str:
    return (value or "").strip()


class OpenAlexClient:
    def __init__(self) -> None:
        self.base = "https://api.openalex.org"
        self.mailto = settings.openalex_email

    def normalize(self, bib: BibliographicData) -> tuple[BibliographicData, str]:
        if not self.mailto:
            return bib, "original"

        params = {"mailto": self.mailto, "per-page": 1}
        if bib.doi:
            response = requests.get(
                f"{self.base}/works/https://doi.org/{bib.doi}",
                params={"mailto": self.mailto},
                timeout=20,
            )
            if response.ok:
                return self._work_to_bib(response.json(), fallback=bib), "openalex"

        query = " ".join(part for part in [bib.title, bib.publication_title, bib.year] if part).strip()
        if not query:
            return bib, "original"

        response = requests.get(
            f"{self.base}/works",
            params={**params, "search": query},
            timeout=20,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return bib, "original"
        return self._work_to_bib(results[0], fallback=bib), "openalex"

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
        candidates = self._search_items(bib.doi or bib.title)
        normalized_title = bib.title.casefold().strip()
        normalized_doi = (bib.doi or "").casefold().strip()
        for item in candidates:
            data = item.get("data", {})
            if data.get("itemType") == "attachment":
                continue
            if normalized_doi and (data.get("DOI") or "").casefold().strip() == normalized_doi:
                return {"key": item.get("key"), "data": data}
            if (data.get("title") or "").casefold().strip() == normalized_title:
                return {"key": item.get("key"), "data": data}
        return None

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

    def _item_payload(self, bib: BibliographicData, request_id: str) -> dict:
        creators = [{"creatorType": "author", "name": creator} for creator in bib.creators]
        item = {
            "itemType": bib.item_type,
            "title": bib.title,
            "creators": creators,
            "publicationTitle": bib.publication_title,
            "date": bib.year,
            "volume": bib.volume or "",
            "issue": bib.issue or "",
            "pages": bib.pages or "",
            "DOI": bib.doi or "",
            "language": bib.language or "",
            "abstractNote": bib.abstract_note or "",
            "tags": [{"tag": self.in_process_tag}],
            "extra": f"Request-ID: {request_id}",
        }
        if self.collection_key:
            item["collections"] = [self.collection_key]
        return item

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers = {"Zotero-API-Version": "3"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers


class FormCycleClient:
    def __init__(self) -> None:
        self.notify_url = settings.formcycle_notify_url
        self.notify_token = settings.formcycle_notify_token

    def send_delivery(self, payload: DeliveryNotificationPayload) -> None:
        if not self.notify_url:
            return

        headers = {"Content-Type": "application/json"}
        if self.notify_token:
            headers["Authorization"] = f"Bearer {self.notify_token}"

        response = requests.post(
            self.notify_url,
            json=json.loads(payload.model_dump_json()),
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
