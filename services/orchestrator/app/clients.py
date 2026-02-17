from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

from app.config import settings
from app.schemas import BibliographicData


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
            raise ValueError(
                "ZOTERO_LIBRARY_TYPE must be 'user' or 'group'."
            )
        library_id = settings.zotero_library_id

        zotero_scope = "users" if library_type == "user" else "groups"
        self.api_key = settings.zotero_api_key
        self.collection_key = settings.zotero_collection_key
        self.base = f"https://api.zotero.org/{zotero_scope}/{library_id}"

    def create_item(
        self,
        bib: BibliographicData,
        request_id: str,
        download_url: str,
        expires_on: str,
    ) -> str:
        endpoint = f"{self.base}/items?key={self.api_key}"
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
            "url": download_url,
            "extra": (
                f"Request-ID: {request_id}\n"
                f"Download-Link-Expires: {expires_on}"
            ),
        }
        if self.collection_key:
            item["collections"] = [self.collection_key]

        response = requests.post(
            endpoint,
            headers={
                "Zotero-API-Version": "3",
                "Content-Type": "application/json",
            },
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


class FormCycleClient:
    def __init__(self) -> None:
        self.notify_url = settings.formcycle_notify_url
        self.notify_token = settings.formcycle_notify_token

    def send_delivery(
        self,
        request_id: str,
        submission_id: str | None,
        user_email: str,
        citation: BibliographicData,
        download_url: str,
        expires_on: str,
        zotero_item_key: str,
    ) -> None:
        if not self.notify_url:
            return

        headers = {"Content-Type": "application/json"}
        if self.notify_token:
            headers["Authorization"] = f"Bearer {self.notify_token}"

        payload = {
            "request_id": request_id,
            "formcycle_submission_id": submission_id,
            "status": "DELIVERED",
            "user_email": user_email,
            "citation": citation.model_dump(),
            "download_url": download_url,
            "expires_on": expires_on,
            "zotero_item_key": zotero_item_key,
        }
        response = requests.post(self.notify_url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
