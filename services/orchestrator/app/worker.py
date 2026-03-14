from __future__ import annotations

import time

from app.config import settings
from app.db import init_db
from app.jobs import process_next_item


def main() -> None:
    init_db()
    while True:
        handled = process_next_item()
        if not handled:
            time.sleep(settings.worker_poll_interval_seconds)


if __name__ == "__main__":
    main()
