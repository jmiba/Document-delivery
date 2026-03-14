# Budibase note

This repository no longer uses Budibase as the primary operator UI.

The active stack is:
- FastAPI for ingestion and internal APIs
- SQLite for persistence
- a polling worker for metadata/Zotero/Nextcloud/FormCycle processing
- Streamlit for the operator dashboard

Budibase-specific files remain only as historical reference for earlier experiments.
