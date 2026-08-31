#!/usr/bin/env python3
"""Produce privacy-safe operational and sales metrics from message log exports."""

from __future__ import annotations

import csv
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


DEFAULT_INPUT = Path("src/clients/tanaka/message_logs_rows.csv")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[int(fraction * (len(ordered) - 1))]


def money_from_checkout(rows: list[dict[str, object]], event_index: int) -> int | None:
    """Find the latest stated checkout total before a strong conversion event."""
    total_pattern = re.compile(
        r"(?:total(?:\s+a\s+(?:pagar|transferir))?|por un total de)\s*:?\s*\$([\d.]+)",
        re.IGNORECASE,
    )
    for row in reversed(rows[:event_index]):
        if row["role"] != "model":
            continue
        amounts = total_pattern.findall(str(row["content"]))
        if amounts:
            return int(amounts[-1].replace(".", ""))
    return None


def main(path: Path) -> None:
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows: list[dict[str, object]] = list(csv.DictReader(source))

    for row in rows:
        row["content"] = row["content"] or ""
        row["timestamp"] = datetime.fromisoformat(str(row["created_at"]))

    conversations: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        conversations[str(row["phone_number"])].append(row)
    for conversation in conversations.values():
        conversation.sort(key=lambda item: item["timestamp"])

    customers = set(conversations)
    catalog_customers = {
        str(row["phone_number"])
        for row in rows
        if row["role"] == "system" and "catalogo_pdf" in str(row["content"])
    }
    engaged = {
        phone
        for phone, conversation in conversations.items()
        if sum(row["role"] == "user" for row in conversation) >= 2
    }
    checkout_pattern = re.compile(r"(?:nombre y apellido|nombre completo)", re.I)
    checkout_customers = {
        str(row["phone_number"])
        for row in rows
        if checkout_pattern.search(str(row["content"]))
        and re.search(r"(?:documento|c[eé]dula)", str(row["content"]), re.I)
        and re.search(r"direcci[oó]n", str(row["content"]), re.I)
    }

    strong_event = re.compile(
        r"(?:comprobante de pago recibido|envi[oó] un comprobante de pago|pedido cerrado)",
        re.I,
    )
    converted: dict[str, int | None] = {}
    for phone, conversation in conversations.items():
        for index, row in enumerate(conversation):
            if row["role"] == "system" and strong_event.search(str(row["content"])):
                converted[phone] = money_from_checkout(conversation, index)
                break

    bot_latencies: list[float] = []
    any_latencies: list[float] = []
    handoff_latencies: list[float] = []
    for conversation in conversations.values():
        for index, row in enumerate(conversation):
            if row["role"] == "user":
                for following in conversation[index + 1 :]:
                    if following["role"] == "user":
                        break
                    if following["role"] in {"model", "asesor"}:
                        delay = (following["timestamp"] - row["timestamp"]).total_seconds()
                        any_latencies.append(delay)
                        if following["role"] == "model":
                            bot_latencies.append(delay)
                        break
            if row["role"] == "system" and str(row["content"]).startswith("HANDOFF"):
                advisor = next(
                    (item for item in conversation[index + 1 :] if item["role"] == "asesor"),
                    None,
                )
                if advisor:
                    handoff_latencies.append(
                        (advisor["timestamp"] - row["timestamp"]).total_seconds()
                    )

    handoffs = [
        row
        for row in rows
        if row["role"] == "system" and str(row["content"]).startswith("HANDOFF")
    ]
    credit_failures = [
        row for row in handoffs if "no había créditos" in str(row["content"])
    ]
    fallback_messages = [
        row for row in rows if "retraso en procesar" in str(row["content"])
    ]
    first_contact_by_day = Counter(
        min(conversation, key=lambda item: item["timestamp"])["timestamp"].date().isoformat()
        for conversation in conversations.values()
    )

    print(f"period={min(row['timestamp'] for row in rows).isoformat()}..{max(row['timestamp'] for row in rows).isoformat()}")
    print(f"messages={len(rows)} customers={len(customers)}")
    print(f"engaged_customers={len(engaged)} ({len(engaged) / len(customers):.1%})")
    print(f"catalog_customers={len(catalog_customers)} ({len(catalog_customers) / len(customers):.1%})")
    print(f"checkout_started={len(checkout_customers)} ({len(checkout_customers) / len(customers):.1%})")
    observed_gmv = sum(value for value in converted.values() if value is not None)
    print(f"strong_conversions={len(converted)} ({len(converted) / len(customers):.1%})")
    print(f"observed_gmv_cop={observed_gmv} average_order_cop={observed_gmv / len(converted):.0f}")
    print(f"handoffs={len(handoffs)} unique_customers={len({str(row['phone_number']) for row in handoffs})}")
    print(f"credit_failure_handoffs={len(credit_failures)} ({len(credit_failures) / len(handoffs):.1%})")
    print(f"fallback_messages={len(fallback_messages)} unique_customers={len({str(row['phone_number']) for row in fallback_messages})}")
    print(f"bot_response_seconds_median={statistics.median(bot_latencies):.1f} p90={percentile(bot_latencies, .9):.1f}")
    print(f"any_response_under_60_seconds={sum(delay <= 60 for delay in any_latencies) / len(any_latencies):.1%}")
    print(f"handoff_to_advisor_minutes_median={statistics.median(handoff_latencies) / 60:.1f} p90={percentile(handoff_latencies, .9) / 60:.1f}")
    print(f"handoff_answered_under_1_hour={sum(delay <= 3600 for delay in handoff_latencies) / len(handoff_latencies):.1%}")
    print("new_customers_by_day=" + ",".join(f"{day}:{count}" for day, count in sorted(first_contact_by_day.items())))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT)
