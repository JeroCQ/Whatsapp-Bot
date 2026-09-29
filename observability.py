"""Structured, low-PII operational events for handoffs and follow-ups."""

import json
from datetime import datetime, timezone

from config import config


def operational_event(event: str, *, phone: str, stage: str, reason: str,
                      attempt: int = 0, scheduled_at=None, result: str = "") -> None:
    payload = {
        "event": event,
        "business_id": config.BUSINESS_ID,
        "conversation": phone,
        "stage": stage,
        "reason": reason,
        "attempt": attempt,
        "scheduled_at": scheduled_at.isoformat() if hasattr(scheduled_at, "isoformat") else scheduled_at,
        "result": result,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    print(f"[OPERATIONAL_EVENT] {json.dumps(payload, ensure_ascii=False, sort_keys=True)}", flush=True)
