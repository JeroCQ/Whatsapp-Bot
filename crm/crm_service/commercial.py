"""Evidence-only commercial projection, independent of conversation control."""

import hashlib
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median

from sqlalchemy import and_, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from crm_service.models import (CommercialEvent, CRMConversation, ModelUsageAttempt, PromotionPermission,
                                SourceMessage, SyncCursor, UsageTurn, now)

CURSOR = "commercial_events"
TERMINAL = {"completed", "declined", "cancelled", "refunded"}
PREFIX = r"^\W*"
ORDER_INTENT = re.compile(r"\b(?:quiero pedir|quiero comprar|voy a comprar|me llev[oa]|deseo pedir|quisiera pedir|hazme el pedido|para mi pedido|hago un pedido)\b")
INQUIRY = re.compile(r"[?¿]|\b(?:precio|cuanto|tienen|manejan|interesa|quiero|cotiza\w*|quisiera|busco)\b")
ACCEPT = re.compile(PREFIX + r"(?:confirmo (?:el|mi) pedido|(?:si[, ]+)?(?:enviamelo|enviamelos|hagamos el pedido)|(?:listo[, ]+)?(?:lo llevo|los llevo|lo compro)|acepto (?:el pedido|la cotizacion))\b")
DECLINE = re.compile(PREFIX + r"(?:no voy a comprar|no hare el pedido|ya no quiero (?:el|mi) pedido|no acepto la cotizacion|no quiero hacer el pedido)\b")
ADVISOR_EVENTS = {
    "paid": re.compile(PREFIX + r"(?:(?:tu|el) )?(?:pago (?:(?:fue|esta) )?(?:confirmado|verificado|validado|recibido)|(?:confirmamos|verificamos|validamos|recibimos) (?:tu|el) pago)\b"),
    "completed": re.compile(PREFIX + r"(?:(?:tu|el) )?pedido (?:ya )?(?:entregado|recogido|fue entregado|fue recogido)\b"),
    "cancelled": re.compile(PREFIX + r"(?:(?:tu|el) )?pedido (?:ya )?(?:cancelado|fue cancelado)\b"),
    "refunded": re.compile(PREFIX + r"(?:reembolso (?:confirmado|realizado)|(?:ya )?te (?:devolvimos|reembolsamos))\b"),
}
MONEY = r"(?:\$\s*|cop\s+)([0-9]{1,3}(?:[.,][0-9]{3}){1,3}|[0-9]{1,9})(?![0-9.,])"
QUOTE = re.compile(r"(?:^|\n)\s*[-*• ]*(?:total a pagar|total del pedido|total)\s*[* :]*" + MONEY, re.I)
SUBTOTAL = re.compile(r"(?:^|\n)\s*[-*• ]*(?:subtotal(?: de productos)?|total de productos)\s*[* :]*" + MONEY, re.I)
CHANGE = re.compile(r"\b(?:otro|nuevo) pedido\b")
# Conservative aliases from the businesses' approved product lines. Longest match
# wins so a product and one of its explicitly named variants aren't both counted.
PRODUCTS = {
    "briosos": ("pan de queso", "pandebono", "buñuelo", "almojábana", "pandeyuca"),
    "tanaka": ("arepa de yuca", "arepa de plátano", "arepa de maduro", "panzerotti", "yogur griego", "trío tanaka"),
    "velvet": ("mochis x6", "mochis x12", "mochis x24", "mochi", "alfajor", "torta de chocolate", "volcán de chocolate"),
    "memos": ("campesino redondo", "queso campesino", "queso costeño", "doble crema tajado", "doble crema", "mozzarella tajado", "mozzarella", "queso criollo", "cuajada"),
}


def folded(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.lower()) if not unicodedata.combining(c))


def units_by_product(business, content):
    result = {}
    for name in sorted(PRODUCTS.get(business, ()), key=len, reverse=True):
        alias = re.escape(folded(name)).replace("tajado", "tajad[oa]")
        match = re.search(rf"\b(?:(\d{{1,4}})\s+(?:(?:cajas?|unidades?|paquetes?)\s+de\s+)?)?{alias}s?\b", content)
        if match:
            result[name] = int(match[1]) if match[1] else None
            content = content[:match.start()] + " " + content[match.end():]
    return result


def worth(match):
    return int(re.sub(r"\D", "", match[1])) if match else None


def facts(row):
    cleaned = folded(row.content.replace("**", "").strip())
    products = units_by_product(row.business_id, cleaned)
    if row.role == "user":
        if DECLINE.search(cleaned):
            return [("declined", None, {})]
        if ACCEPT.search(cleaned):
            return [("accepted", None, products)]
        if ORDER_INTENT.search(cleaned):
            return [("intent", None, products)]
        if products and INQUIRY.search(cleaned):
            return [("interest", None, products)]
    if row.role == "asesor" and row.delivery_status in {"accepted", "sent", "delivered", "read"}:
        for kind, pattern in ADVISOR_EVENTS.items():
            matched = pattern.search(cleaned)
            if matched:
                # Extract only an explicit amount immediately attached to the
                # confirmation. A quote elsewhere in the message is not spend.
                amount = re.match(r"[ :]*(?:por |de )?" + MONEY, cleaned[matched.end():])
                return [(kind, worth(amount), products)]
    if row.role == "model" or (row.role == "asesor" and row.delivery_status in {"accepted", "sent", "delivered", "read"}):
        quote = QUOTE.search(cleaned)
        if quote or SUBTOTAL.search(cleaned):
            return [("quoted", worth(quote), products)]
    return []


def project(session, row):
    observed = facts(row)
    if not observed:
        return
    scope = (CommercialEvent.business_id == row.business_id, CommercialEvent.phone_number == row.phone_number)
    previous = session.scalar(select(CommercialEvent).where(*scope, CommercialEvent.order_id.is_not(None),
        CommercialEvent.source_id < row.source_id, CommercialEvent.occurred_at <= row.created_at)
        .order_by(CommercialEvent.source_id.desc()).limit(1))
    crm = session.get(CRMConversation, (row.business_id, row.phone_number))
    queue = (crm.classification or {}).get("attention_queue", "retail") if crm and row.business_id == "memos" else None
    for kind, amount, items in observed:
        active = previous and previous.kind not in TERMINAL and (row.created_at - previous.occurred_at <= timedelta(days=7)
                 or previous.kind in {"accepted", "paid"})
        order = previous.order_id if active else None
        if kind == "intent" and CHANGE.search(folded(row.content)):
            order = None
        if kind == "accepted" and not items and (not active or previous.kind not in {"quoted", "intent"}):
            continue
        if kind in {"intent", "quoted", "accepted"} and order is None:
            # A bare acceptance has to refer to an identifiable product/order.
            if kind == "accepted" and not items:
                continue
            order = row.source_id
        if kind in {"paid", "completed", "cancelled", "declined", "refunded"}:
            if kind == "refunded" and previous and previous.kind in {"paid", "completed"}:
                order = previous.order_id
            if order is None:
                continue
        session.execute(insert(CommercialEvent).values(business_id=row.business_id, source_id=row.source_id,
            kind=kind, phone_number=row.phone_number, order_id=order if kind != "interest" else None,
            queue=queue, occurred_at=row.created_at, amount_cop=amount,
            product_subtotal_cop=worth(SUBTOTAL.search(folded(row.content))) if kind in {"quoted", "paid"} else None,
            items=items).on_conflict_do_nothing())


def consume_commercial(settings, sessions, business_id=None):
    for business in settings.customer_insights_businesses:
        if business_id and business != business_id:
            continue
        with sessions.begin() as session:
            lock = int.from_bytes(hashlib.sha256(f"commercial:{business}".encode()).digest()[:8], "big", signed=True)
            if not session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock}):
                continue
            cursor = session.get(SyncCursor, (business, CURSOR))
            if cursor is None:
                # Start measurement at activation. Historical messages can still
                # inform customer memory via the independent return analysis.
                newest = session.scalar(select(func.max(SourceMessage.source_id)).where(SourceMessage.business_id == business))
                session.add(SyncCursor(business_id=business, resource=CURSOR,
                                       value={"after": newest or 0, "activated_at": now().isoformat()}))
                continue
            rows = session.scalars(select(SourceMessage).where(SourceMessage.business_id == business,
                SourceMessage.source_id > cursor.value["after"])
                .order_by(SourceMessage.source_id).limit(200)).all()
            if not rows:
                continue
            activated_at = datetime.fromisoformat(cursor.value["activated_at"])
            for row in rows:
                if row.created_at >= activated_at and not row.is_delivery_event:
                    project(session, row)
            cursor.value = {**cursor.value, "after": rows[-1].source_id}
            cursor.updated_at = now()


def report_sales(session, business, start, end, queue=None, sender_id="", permission=None):
    scope = [CommercialEvent.business_id == business, CommercialEvent.occurred_at >= start, CommercialEvent.occurred_at < end]
    window = select(CommercialEvent).where(*scope).order_by(CommercialEvent.source_id).limit(20001)
    rows = list(session.scalars(window))
    if len(rows) > 20000:
        raise OverflowError("commercial_report_window_too_large")
    states = dict(session.execute(select(PromotionPermission.phone_number, PromotionPermission.status).where(
        PromotionPermission.business_id == business, PromotionPermission.sender_id == sender_id)).all())

    def permission_for(phone):
        state = states.get(phone, "unknown")
        return "no" if state in {"denied", "revoked"} else "yes" if state == "granted" else "unknown"

    counts = Counter()
    amounts = defaultdict(Counter)
    order_groups = defaultdict(list)
    inquiries = Counter()
    paid_items = defaultdict(Counter)
    contacts = set()
    for row in rows:
        if queue and row.queue != queue or permission and permission_for(row.phone_number) != permission:
            continue
        contacts.add(row.phone_number)
        if row.kind in {"interest", "intent"}:
            inquiries.update((row.phone_number, name) for name in row.items)
        if row.order_id is not None:
            order_groups[row.order_id].append(row)
    # Bring in the start and earlier events for orders with activity in this window.
    if order_groups:
        for row in session.scalars(select(CommercialEvent).where(CommercialEvent.business_id == business,
            CommercialEvent.order_id.in_(order_groups), CommercialEvent.occurred_at < start)
            .order_by(CommercialEvent.source_id)):
            order_groups[row.order_id].append(row)
    elapsed_payment, elapsed_completion = [], []
    for events in order_groups.values():
        events.sort(key=lambda row: (row.occurred_at, row.source_id))
        paid = next((row for row in events if row.kind == "paid"), None)
        done = next((row for row in events if row.kind == "completed"), None)
        for kind in {row.kind for row in events if start <= row.occurred_at < end}:
            counts[kind] += 1
        if business == "memos" and any(row.kind == "quoted" and start <= row.occurred_at < end
                                       and (row.product_subtotal_cop or 0) >= 300_000 for row in events):
            counts["quoted_at_least_300k"] += 1
        current = events[-1]
        if events[0].occurred_at >= start:
            if paid and current.kind not in {"cancelled", "refunded"}:
                counts["cohort_sold"] += 1
            elif current.kind in {"declined", "cancelled", "refunded"}:
                counts["cohort_lost"] += 1
            else:
                counts["cohort_unresolved"] += 1
        if current.kind not in TERMINAL | {"accepted", "paid"}:
            cutoff = min(end, now())
            stale = cutoff - current.occurred_at >= timedelta(days=7)
            counts["inactive" if stale else "unresolved"] += 1
        if paid and start <= paid.occurred_at < end:
            elapsed_payment.append((paid.occurred_at - events[0].occurred_at).total_seconds())
            if paid.amount_cop is not None:
                amounts[paid.phone_number]["spend"] += paid.amount_cop
                counts["paid_with_amount"] += 1
            if business == "memos" and (paid.product_subtotal_cop or 0) >= 300_000:
                counts["paid_at_least_300k"] += 1
            amounts[paid.phone_number]["paid"] += 1
            # Only a paid confirmation or an explicitly accepted item list
            # can attribute paid products. Quoted suggestions are excluded.
            items = paid.items or next((row.items for row in reversed(events) if row.kind == "accepted"
                                        and row.items and row.occurred_at <= paid.occurred_at), {})
            for name, units in items.items():
                paid_items[name]["paid_orders"] += 1
                if units is not None:
                    paid_items[name]["units"] += units
            counts["paid_with_products"] += bool(items)
        if done and start <= done.occurred_at < end:
            elapsed_completion.append((done.occurred_at - events[0].occurred_at).total_seconds())
            amounts[done.phone_number]["completed"] += 1
        for event in events:
            if event.kind == "refunded" and start <= event.occurred_at < end and event.amount_cop is not None:
                amounts[event.phone_number]["spend"] -= event.amount_cop
                counts["refunds_with_amount"] += 1
    demand = Counter(name for (_, name), total in inquiries.items() if total > 1)
    totals = Counter(permission_for(phone) for phone in contacts)
    resolved = counts["cohort_sold"] + counts["cohort_lost"]
    return {
        "counts": dict(counts), "orders_observed": len(order_groups), "permission_counts": dict(totals),
        "resolved_conversion": counts["cohort_sold"] / resolved if resolved else None,
        "net_spend_cop": sum(row["spend"] for row in amounts.values()) if counts["paid_with_amount"] or counts["refunds_with_amount"] else None,
        "median_seconds_to_payment": median(elapsed_payment) if elapsed_payment else None,
        "median_seconds_to_completion": median(elapsed_completion) if elapsed_completion else None,
        "products": [{"name": name, "repeat_inquiries": demand[name], **paid_items[name]}
                     for name in sorted(set(demand) | set(paid_items))],
    }


def customer_metrics(session, business, phone):
    scope = (CommercialEvent.business_id == business, CommercialEvent.phone_number == phone)
    events = session.execute(select(CommercialEvent.order_id, CommercialEvent.kind, func.max(CommercialEvent.amount_cop))
                             .where(*scope, CommercialEvent.kind.in_(("paid", "completed", "refunded")))
                             .group_by(CommercialEvent.order_id, CommercialEvent.kind)).all()
    paid = [amount for _, kind, amount in events if kind == "paid"]
    refunded = [amount for _, kind, amount in events if kind == "refunded"]
    known_amounts = [amount for amount in paid if amount is not None]
    return {"paid_orders": len(paid), "completed_orders": sum(kind == "completed" for _, kind, _ in events),
            "paid_with_amount": len(known_amounts),
            "net_spend_cop": sum(known_amounts) - sum(amount for amount in refunded if amount is not None)
            if known_amounts else None}


def handoff_metrics(session, business, start, end):
    reply = aliased(SourceMessage)
    handoff = aliased(SourceMessage)
    following = aliased(SourceMessage)
    next_handoff = select(func.min(following.created_at)).where(
        following.business_id == business, following.phone_number == handoff.phone_number,
        following.message_metadata["source"].as_string() == "crm_handoff", following.created_at > handoff.created_at,
    ).correlate(handoff).scalar_subquery()
    first = select(func.min(reply.created_at)).where(reply.business_id == business,
        reply.phone_number == handoff.phone_number, reply.role == "asesor", reply.created_at > handoff.created_at,
        reply.created_at < func.coalesce(next_handoff, end),
        reply.delivery_status.in_(("accepted", "sent", "delivered", "read")),
    ).correlate(handoff).scalar_subquery()
    rows = session.execute(select(handoff.created_at, first).where(handoff.business_id == business,
        handoff.message_metadata["source"].as_string() == "crm_handoff",
        handoff.created_at >= start, handoff.created_at < end).limit(20001)).all()
    durations = [(replied - opened).total_seconds() for opened, replied in rows[:20000] if replied is not None]
    return {"handoffs": min(len(rows), 20000), "with_reply": len(durations),
            "median_seconds": median(durations) if durations else None, "partial_window": len(rows) > 20000}


def usage_metrics(session, business, start, end, phone=None):
    filters = [ModelUsageAttempt.business_id == business, ModelUsageAttempt.observed_at >= start,
               ModelUsageAttempt.observed_at < end]
    if phone:
        filters.append(UsageTurn.conversation_id == phone)
    attempts = session.execute(select(ModelUsageAttempt.model_version, ModelUsageAttempt.observed_at,
                                      ModelUsageAttempt.input_tokens, ModelUsageAttempt.output_tokens,
                                      ModelUsageAttempt.cached_input_tokens, ModelUsageAttempt.thought_tokens)
        .join(UsageTurn, and_(UsageTurn.business_id == ModelUsageAttempt.business_id,
                              UsageTurn.turn_id == ModelUsageAttempt.turn_id))
        .where(*filters).limit(20001)).all()
    try:
        configured = json.loads(os.environ.get("CRM_COMMERCIAL_MODEL_RATES", "{}"))
        rates = {model: sorted(((date.fromisoformat(entry["from"]),
                  tuple(Decimal(str(entry[k])) for k in ("input", "cached_input", "output"))) for entry in entries),
                  reverse=True) for model, entries in configured.items()}
    except (AttributeError, KeyError, TypeError, ValueError, InvalidOperation):
        rates = {}
    total = Decimal(0)
    priced = with_tokens = 0
    for version, at, input_tokens, output_tokens, cached, thoughts in attempts[:20000]:
        if input_tokens is None or output_tokens is None:
            continue
        with_tokens += 1
        rate = next((entry for active_on, entry in rates.get(version, ()) if active_on <= at.date()), None)
        if rate and cached is not None and thoughts is not None and 0 <= cached <= input_tokens and all(
            value.is_finite() and value >= 0 for value in rate
        ):
            total += ((input_tokens - cached) * rate[0] + cached * rate[1] + (output_tokens + thoughts) * rate[2]) / 1_000_000
            priced += 1
    return {"attempts": min(len(attempts), 20000), "attempts_with_tokens": with_tokens, "priced_attempts": priced,
            "partial_window": len(attempts) > 20000, "estimated_gemini_usd": float(total) if priced else None,
            "meta_charges": None}
