"""Explicit single bounded pilot projection step; never a queue/follow-up consumer."""
from dataclasses import replace
import argparse
import signal
import threading
from typing import Any
from crm_service.budget import ReadBudget
from crm_service.bot_source import BotSourceAdapter
from crm_service.db import database
from crm_service.models import PilotPublication, PilotSwitch
from crm_service.pilot import BUSINESS, PHONE
from crm_service.pilot_app import pilot_settings
from crm_service.settings import Business, flag
from crm_service.source import SourceFailure
from crm_service.sync import Synchronizer


class PilotReader(BotSourceAdapter):
    def page(self, table: str, *, after: str | int | None, limit: int,
             phone_number: str | None = None) -> list[dict[str, Any]]:
        if phone_number not in {None, PHONE}:
            raise SourceFailure("pilot_source_conversation_rejected")
        rows = super().page(table, after=after, limit=limit, phone_number=PHONE)
        if any(row.get("phone_number") != PHONE or row.get("business_id", BUSINESS) != BUSINESS for row in rows):
            raise SourceFailure("pilot_source_row_scope_rejected")
        return rows


class PilotSynchronizer(Synchronizer):
    def reader(self, business: Business, budget: ReadBudget) -> PilotReader:
        if business.id != BUSINESS:
            raise SourceFailure("pilot_source_business_rejected")
        return PilotReader(business, budget=budget)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scoped pilot sync, no live queue/notification consumption")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if flag("CHATTY_PILOT_STANDBY", True):
        if args.once:
            raise SystemExit("pilot_standby_no_source_reads")
        # Standby has no DB/source client, queue listener, dispatcher or Meta webhook.
        while not stop.wait(30):
            pass
        return
    settings = pilot_settings("worker")
    engine, sessions = database(settings)
    try:
        while not stop.is_set():
            with sessions.begin() as session:
                state = session.get(PilotSwitch, BUSINESS)
                publication = session.get(PilotPublication, state.publication_version) if state else None
                active = state is not None and state.mode == "shared" and publication is not None
                values = publication.snapshot["configuration"] if active else {}
            if active:
                scoped = replace(settings, **{key: values[key] for key in (
                    "sync_interval", "page_size", "max_pages", "sync_max_seconds")})
                # Existing budgets, cursors, deduplication, partial-baseline and backoff rules.
                print(PilotSynchronizer(scoped, sessions).run(BUSINESS))
            if args.once:
                if not active:
                    raise SystemExit("pilot_not_active_no_source_reads")
                return
            stop.wait(values.get("sync_interval", settings.sync_interval))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
