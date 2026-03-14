from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:////app/data/delivery.sqlite3"
    scan_input_dir: str = "/scans"
    work_dir: str = "/app/data/work"
    worker_poll_interval_seconds: int = 15
    attachment_poll_interval_seconds: int = 300
    notification_retry_interval_seconds: int = 900
    default_link_expiry_days: int = 14
    normalization_auto_accept_threshold: float = 0.92

    openalex_email: str | None = None

    nextcloud_base_url: str
    nextcloud_username: str
    nextcloud_password: str
    nextcloud_dav_base_path: str = "/remote.php/dav/files/{username}"
    nextcloud_root_path: str = "/Digitization"

    zotero_library_type: str = "user"
    zotero_library_id: str
    zotero_api_key: str
    zotero_collection_key: str | None = None
    zotero_in_process_tag: str = "in process"

    formcycle_webhook_secret: str | None = None
    formcycle_notify_url: str | None = None
    formcycle_notify_token: str | None = None
    internal_api_token: str | None = None

    ocr_command_template: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
