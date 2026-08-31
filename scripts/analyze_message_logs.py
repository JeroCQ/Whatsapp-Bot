#!/usr/bin/env python3
"""Produce privacy-safe operational and sales metrics from message log exports."""

from __future__ import annotations

import csv
import argparse
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


DEFAULT_INPUT = Path("src/clients/tanaka/message_logs_rows.csv")
CREDIT_FAILURE = re.compile(r"no hab[ií]a cr[eé]ditos|no hay cr[eé]ditos", re.I)
SALE_EVIDENCE = re.compile(
    r"comprobante de pago recibido|envi[oó] un comprobante de pago|pedido cerrado|"
    r"gracias[^\n]{0,30}por tu compra|paso a despachos|despachar tu pedido",
    re.I,
)
NO_SALE_EVIDENCE = re.compile(
    r"(?:no|as[ií] no) (?:se puede|voy a (?:comprar|pedir))|m[aá]s adelante (?:le )?compr",
    re.I,
)


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


def resolved_sessions(conversations: dict[str, list[dict[str, object]]]) -> list[list[dict[str, object]]]:
    """Return each closed Chatwoot interval, including messages after its handoff."""
    sessions = []
    for conversation in conversations.values():
        start = 0
        for index, row in enumerate(conversation):
            if row["role"] == "system" and str(row["content"]).startswith("RESOLVED:"):
                sessions.append(conversation[start : index + 1])
                start = index + 1
    return sessions


def resolution_outcome(session: list[dict[str, object]]) -> str:
    combined = "\n".join(str(row["content"]) for row in session)
    if SALE_EVIDENCE.search(combined):
        return "sold"
    if NO_SALE_EVIDENCE.search(combined):
        return "not_sold"
    return "unknown"


def post_resolution_segments(
    conversations: dict[str, list[dict[str, object]]],
) -> list[list[dict[str, object]]]:
    """Capture activity after each resolution until that ticket is resolved again."""
    segments = []
    for conversation in conversations.values():
        for index, row in enumerate(conversation):
            if row["role"] != "system" or not str(row["content"]).startswith("RESOLVED:"):
                continue
            segment = []
            for following in conversation[index + 1 :]:
                if following["role"] == "system" and str(following["content"]).startswith("RESOLVED:"):
                    break
                segment.append(following)
            segments.append(segment)
    return segments


def main(path: Path, show_phones: bool = False) -> None:
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

    all_customers = set(conversations)
    excluded_credit_customers = {
        phone
        for phone, conversation in conversations.items()
        if any(CREDIT_FAILURE.search(str(row["content"])) for row in conversation)
    }
    customers = all_customers - excluded_credit_customers
    # Effectiveness metrics use only conversations never touched by the credit
    # outage. Mixing them in would attribute infrastructure failure to the bot.
    rows = [row for row in rows if str(row["phone_number"]) in customers]
    conversations = {phone: conversation for phone, conversation in conversations.items() if phone in customers}
    catalog_customers = {
        str(row["phone_number"])
        for row in rows
        if row["role"] == "system" and "catalogo_pdf" in str(row["content"])
    }
    catalog_missing = customers - catalog_customers
    engaged = {
        phone
        for phone, conversation in conversations.items()
        if sum(row["role"] == "user" for row in conversation) >= 2
    }
    checkout_pattern = re.compile(r"(?:nombre y apellido|nombre completo)", re.I)
    checkout_forms = {
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
    quoted_customers = {
        str(row["phone_number"])
        for row in rows
        if row["role"] in {"model", "asesor"}
        and re.search(
            r"(?:total(?: a pagar| a transferir| productos)?\s*:|total ser[ií]a)",
            str(row["content"]),
            re.I,
        )
    }

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
            if (
                row["role"] == "system"
                and str(row["content"]).startswith("HANDOFF")
                and not CREDIT_FAILURE.search(str(row["content"]))
            ):
                advisor = next(
                    (
                        item
                        for item in conversation[index + 1 :]
                        if item["role"] == "asesor"
                        or (
                            item["role"] == "system"
                            and str(item["content"]).startswith(("HANDOFF", "RESOLVED:"))
                        )
                    ),
                    None,
                )
                if advisor and advisor["role"] == "asesor":
                    handoff_latencies.append(
                        (advisor["timestamp"] - row["timestamp"]).total_seconds()
                    )

    handoffs = [
        row
        for row in rows
        if row["role"] == "system" and str(row["content"]).startswith("HANDOFF")
    ]
    operational_handoffs = [row for row in handoffs if not CREDIT_FAILURE.search(str(row["content"]))]
    first_contact_by_day = Counter(
        min(conversation, key=lambda item: item["timestamp"])["timestamp"].date().isoformat()
        for conversation in conversations.values()
    )
    closed_sessions = resolved_sessions(conversations)
    outcomes = Counter(resolution_outcome(session) for session in closed_sessions)
    after_resolved = post_resolution_segments(conversations)
    returned_after_resolved = [
        segment for segment in after_resolved if any(row["role"] == "user" for row in segment)
    ]
    post_outcomes = Counter(resolution_outcome(segment) for segment in returned_after_resolved)

    print(f"period={min(row['timestamp'] for row in rows).isoformat()}..{max(row['timestamp'] for row in rows).isoformat()}")
    print(f"excluded_credit_customers={len(excluded_credit_customers)}")
    print(f"messages={len(rows)} evaluated_customers={len(customers)}")
    print(f"engaged_customers={len(engaged)} ({len(engaged) / len(customers):.1%})")
    print(f"catalog_customers={len(catalog_customers)} ({len(catalog_customers) / len(customers):.1%})")
    print(f"catalog_missing={len(catalog_missing)}")
    print(f"checkout_forms_offered={len(checkout_forms)} ({len(checkout_forms) / len(customers):.1%})")
    print(f"orders_with_total_quote={len(quoted_customers)} ({len(quoted_customers) / len(customers):.1%})")
    observed_gmv = sum(value for value in converted.values() if value is not None)
    print(f"strong_conversions={len(converted)} ({len(converted) / len(customers):.1%})")
    print(f"observed_gmv_cop={observed_gmv} average_order_cop={observed_gmv / len(converted):.0f}")
    print(f"operational_handoffs={len(operational_handoffs)} unique_customers={len({str(row['phone_number']) for row in operational_handoffs})}")
    print(f"resolved_sessions={sum(outcomes.values())} sold={outcomes['sold']} not_sold={outcomes['not_sold']} unknown={outcomes['unknown']}")
    print(f"returned_after_resolved={len(returned_after_resolved)} sold_after_resolved={post_outcomes['sold']} not_sold_after_resolved={post_outcomes['not_sold']} unknown_after_resolved={post_outcomes['unknown']}")
    print(f"bot_response_seconds_median={statistics.median(bot_latencies):.1f} p90={percentile(bot_latencies, .9):.1f}")
    print(f"any_response_under_60_seconds={sum(delay <= 60 for delay in any_latencies) / len(any_latencies):.1%}")
    print(f"operational_handoff_to_advisor_minutes_median={statistics.median(handoff_latencies) / 60:.1f} p90={percentile(handoff_latencies, .9) / 60:.1f}")
    print(f"operational_handoff_answered_under_1_hour={sum(delay <= 3600 for delay in handoff_latencies) / len(handoff_latencies):.1%}")
    print("new_customers_by_day=" + ",".join(f"{day}:{count}" for day, count in sorted(first_contact_by_day.items())))
    if show_phones:
        print("checkout_form_without_strong_conversion=" + ",".join(sorted(checkout_forms - set(converted))))
        print("quoted_order_without_strong_conversion=" + ",".join(sorted(quoted_customers - set(converted))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--show-phones", action="store_true", help="Include phone numbers for manual audit")
    args = parser.parse_args()
    main(args.path, show_phones=args.show_phones)
