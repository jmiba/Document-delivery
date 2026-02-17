from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    redis_url: str = "redis://redis:6379/0"
    queue_name: str = "digitization"
    scan_input_dir: str = "/scans"
    default_link_expiry_days: int = 14

    nextcloud_base_url: str
    nextcloud_username: str
    nextcloud_password: str
    nextcloud_dav_base_path: str = "/remote.php/dav/files/{username}"
    nextcloud_root_path: str = "/Digitization"

    zotero_library_type: str = "user"
    zotero_library_id: str
    zotero_api_key: str
    zotero_collection_key: str | None = None

    formcycle_webhook_secret: str | None = None
    formcycle_notify_url: str | None = None
    formcycle_notify_token: str | None = None
    internal_api_token: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
