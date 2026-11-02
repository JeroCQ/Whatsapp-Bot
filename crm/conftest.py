"""Keep the independent CRM suite separate from the parent bot's test discovery.

Run the CRM suite from crm/ (or pytest crm/); it requires its own dependencies
and disposable PostgreSQL database. The bot suite must not inherit those needs.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    if collection_path == ROOT / "tests" and config.rootpath != ROOT:
        return True
    return None
