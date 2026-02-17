from pydantic import BaseModel, Field


class BibliographicData(BaseModel):
    item_type: str = "journalArticle"
    title: str
    creators: list[str] = Field(default_factory=list)
    publication_title: str
    year: str
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    doi: str | None = None
    language: str | None = None
    abstract_note: str | None = None


class FormCycleEvent(BaseModel):
    request_id: str
    formcycle_submission_id: str | None = None
    event_type: str = "STATUS_CHANGED"
    status: str
    user_email: str
    user_name: str | None = None
    ocr_pdf_filename: str
    bibliographic_data: BibliographicData


class DeliveryResult(BaseModel):
    request_id: str
    status: str
    download_url: str
    expires_on: str
    zotero_item_key: str
