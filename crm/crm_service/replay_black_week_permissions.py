"""Project Black Week choices retained by the existing sync before opt-in tables were ready."""

import argparse

from sqlalchemy import select, text

from crm_service.db import database
from crm_service.models import SourceMessage
from crm_service.promotion_permissions import BLACK_WEEK_SENDERS, BLACK_WEEK_VERSION, project_permission_message
from crm_service.settings import Settings
from crm_service.sync import lock_id

PAGE_SIZE = 100


def replay(sessions, business):
    last_id, processed = 0, 0
    while True:
        with sessions.begin() as session:
            session.execute(text("SET LOCAL lock_timeout = '2s'"))
            session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": lock_id(business)})
            rows = list(session.scalars(select(SourceMessage).where(
                SourceMessage.business_id == business,
                SourceMessage.source_id > last_id,
                SourceMessage.role == "system",
                SourceMessage.message_metadata["source"].as_string() == "promotion_permission",
                SourceMessage.message_metadata["permission_version"].as_string() == BLACK_WEEK_VERSION,
            ).order_by(SourceMessage.source_id).limit(PAGE_SIZE)))
            for row in rows:
                project_permission_message(session, row)
                last_id = row.source_id
        processed += len(rows)
        if len(rows) < PAGE_SIZE:
            return processed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("business", choices=sorted(BLACK_WEEK_SENDERS))
    args = parser.parse_args()
    settings = Settings.from_env(service="worker")
    if args.business not in settings.businesses:
        parser.error("Business is not registered in this worker")
    engine, sessions = database(settings)
    try:
        print(f"Reviewed {replay(sessions, args.business)} Black Week permission events for {args.business}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
