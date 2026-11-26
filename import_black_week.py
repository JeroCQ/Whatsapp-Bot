"""Import confirmed external Black Week sends into the current business history."""

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

from black_week import campaign_summary, message_id
from webhook_utils import valid_whatsapp_sender


def read_report(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not {"recipient", "sent_at"}.issubset(reader.fieldnames or []):
            raise ValueError("The report requires recipient and sent_at")
        rows = []
        identities = {}
        for row in reader:
            phone = (row.get("recipient") or "").strip().removeprefix("+")
            wamid = (row.get("wamid") or "").strip() or None
            try:
                sent_at = datetime.fromisoformat(row.get("sent_at") or "")
            except ValueError:
                raise ValueError(f"Invalid send time on CSV line {reader.line_num}") from None
            if (not valid_whatsapp_sender(phone) or (wamid is not None and not message_id(wamid)) or
                    sent_at.tzinfo is None or sent_at > datetime.now(timezone.utc)):
                raise ValueError(f"Invalid historical send on CSV line {reader.line_num}")
            sent_at = sent_at.astimezone(timezone.utc)
            identity = wamid or (phone, sent_at)
            previous = identities.get(identity)
            if previous is not None and previous != (phone, sent_at):
                raise ValueError(f"Conflicting message identity on CSV line {reader.line_num}")
            if previous is None:
                identities[identity] = (phone, sent_at)
                rows.append((phone, wamid, sent_at))
        return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--apply", action="store_true", help="Write the verified send report")
    args = parser.parse_args()
    rows = read_report(args.report)
    if not args.apply:
        print(f"Verified report format: {len(rows)} unique sends")
        return

    from config import config
    from database import get_or_create_customer_state, store_campaign_exposure

    if not campaign_summary(config.BUSINESS_ID, config.WA_PHONE_NUMBER_ID):
        parser.error("Run the import in the registered Tanaka or Velvet bot service")
    observed_at = datetime.now(timezone.utc)
    for phone, wamid, sent_at in rows:
        if not get_or_create_customer_state(phone):
            raise RuntimeError("Could not load contact for campaign history")
        store_campaign_exposure(phone, wamid, observed_at, "reported_send", sent_at=sent_at)
    print(f"Imported {len(rows)} verified sends for {config.BUSINESS_ID}")


if __name__ == "__main__":
    main()
