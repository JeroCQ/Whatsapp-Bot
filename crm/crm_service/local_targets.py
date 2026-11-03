"""Fail closed: this branch cannot connect to any productive source."""

import re

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from crm_service.source import SourceFailure


def require_disposable_source(value: str) -> None:
    try:
        url = make_url(value)
        if (
            url.drivername not in {"postgresql", "postgresql+psycopg"}
            or url.host not in {"localhost", "127.0.0.1", "::1"}
            or url.query
            or not re.fullmatch(r"crm_(?:test[a-z0-9_]*|staging_[a-z0-9_]+)", url.database or "")
        ):
            raise ValueError
    except (ValueError, TypeError, ArgumentError):
        raise SourceFailure("production_sources_blocked_requires_permission_review") from None
