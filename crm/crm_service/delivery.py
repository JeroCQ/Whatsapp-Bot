"""Incremental, tenant-scoped delivery projection; source facts remain unchanged."""

import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from crm_service.models import DeliveryReceipt, SourceMessage

STATUS_RANK = {"unknown": 0, "accepted": 1, "failed": 2, "delivered": 3, "read": 4}
OUTBOUND_ROLES = ("asesor", "model")


def status(value: Any) -> str | None:
    if value == "sent":
        return "accepted"
    return value if isinstance(value, str) and value in STATUS_RANK else None


def merge_status(current: str | None, incoming: str | None) -> str | None:
    """A timeout cannot erase evidence, nor can a late failure erase delivery."""
    current, incoming = status(current), status(incoming)
    if incoming is None:
        return current
    if current is None or STATUS_RANK[incoming] > STATUS_RANK[current]:
        return incoming
    return current


def identity(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str) or not 0 < len(value) <= maximum:
        return None
    return value if all(ord(character) >= 32 for character in value) else None


def is_delivery_event(row: SourceMessage) -> bool:
    metadata = row.message_metadata or {}
    return bool(
        row.is_delivery_event
        or (
            row.role == "system"
            and metadata.get("source") == "crm_delivery"
            and row.content.startswith("DELIVERY_STATUS:")
        )
    )


def visible_message_clause() -> ColumnElement[bool]:
    return SourceMessage.is_delivery_event.is_(False)


def _matching_receipts(row: SourceMessage) -> list[ColumnElement[bool]]:
    comparisons = []
    if row.command_id:
        comparisons.append(DeliveryReceipt.command_id == row.command_id)
    if row.provider_message_id:
        comparisons.append(DeliveryReceipt.provider_message_id == row.provider_message_id)
    return comparisons


def _matching_messages(receipt: DeliveryReceipt) -> list[ColumnElement[bool]]:
    comparisons = []
    if receipt.command_id:
        comparisons.append(SourceMessage.command_id == receipt.command_id)
    if receipt.provider_message_id:
        comparisons.append(SourceMessage.provider_message_id == receipt.provider_message_id)
    return comparisons


def _revision(session: Session, row: SourceMessage) -> None:
    # Synchronizer holds its per-business transaction lock. Sequence values are
    # independent from source IDs, so a late original gets a fresh checkpoint.
    row.delivery_revision = int(session.scalar(text("SELECT nextval('crm_delivery_revision_seq')")) or 0)


def _apply_receipt(session: Session, receipt: DeliveryReceipt) -> None:
    comparisons = _matching_messages(receipt)
    if not comparisons:
        return
    query = select(SourceMessage).where(
        SourceMessage.business_id == receipt.business_id,
        SourceMessage.phone_number == receipt.phone_number,
        SourceMessage.role.in_(OUTBOUND_ROLES),
        visible_message_clause(),
        or_(*comparisons),
    )
    # If both sides know an identity, a conflicting one must never be ignored.
    if receipt.command_id:
        query = query.where(
            or_(SourceMessage.command_id.is_(None), SourceMessage.command_id == receipt.command_id)
        )
    if receipt.provider_message_id:
        query = query.where(
            or_(
                SourceMessage.provider_message_id.is_(None),
                SourceMessage.provider_message_id == receipt.provider_message_id,
            )
        )
    candidates = session.scalars(query.limit(2)).all()
    if len(candidates) != 1:
        # Keep the unmatched fact for a later original or for data-quality review.
        return
    target = candidates[0]
    best = merge_status(target.delivery_status, receipt.status)
    command = target.command_id or receipt.command_id
    provider = target.provider_message_id or receipt.provider_message_id
    changed = (target.delivery_status, target.command_id, target.provider_message_id) != (
        best,
        command,
        provider,
    )
    target.delivery_status, target.command_id, target.provider_message_id = best, command, provider
    receipt.matched_source_id = target.source_id
    if changed:
        _revision(session, target)
    session.flush()


def project_message(session: Session, row: SourceMessage) -> None:
    """Call once after every source upsert, including bounded reconciliation."""
    metadata = row.message_metadata or {}
    row.is_delivery_event = is_delivery_event(row)
    if row.is_delivery_event:
        received_status = status(metadata.get("delivery_status"))
        command = identity(metadata.get("command_id") or metadata.get("source_event_id"), 160)
        provider = identity(metadata.get("provider_message_id"), 300)
        if received_status is None or not (command or provider):
            return
        receipt = session.get(DeliveryReceipt, (row.business_id, row.source_id))
        if receipt is None:
            receipt = DeliveryReceipt(
                business_id=row.business_id,
                event_source_id=row.source_id,
                phone_number=row.phone_number,
                command_id=command,
                provider_message_id=provider,
                status=received_status,
                received_at=row.created_at,
            )
            session.add(receipt)
        elif (receipt.phone_number, receipt.command_id, receipt.provider_message_id) != (
            row.phone_number,
            command,
            provider,
        ):
            # An existing fact cannot be rebound to another command/conversation.
            return
        else:
            receipt.status = merge_status(receipt.status, received_status) or received_status
        session.flush()
        _apply_receipt(session, receipt)
        return
    if row.role not in OUTBOUND_ROLES:
        return
    previous = (row.delivery_status, row.command_id, row.provider_message_id)
    command = identity(metadata.get("command_id") or metadata.get("source_event_id"), 160)
    provider = identity(metadata.get("provider_message_id"), 300)
    # Immutable known identities cannot be rebound by a stale metadata sweep.
    if command and (not row.command_id or row.command_id == command):
        row.command_id = command
    if provider and (not row.provider_message_id or row.provider_message_id == provider):
        row.provider_message_id = provider
    row.delivery_status = merge_status(row.delivery_status, status(metadata.get("delivery_status")))
    if previous != (row.delivery_status, row.command_id, row.provider_message_id):
        _revision(session, row)
    session.flush()
    comparisons = _matching_receipts(row)
    if comparisons:
        for receipt in session.scalars(
            select(DeliveryReceipt)
            .where(
                DeliveryReceipt.business_id == row.business_id,
                DeliveryReceipt.phone_number == row.phone_number,
                DeliveryReceipt.matched_source_id.is_(None),
                or_(*comparisons),
            )
            .order_by(DeliveryReceipt.event_source_id)
        ):
            _apply_receipt(session, receipt)


def delivery_watermark(session: Session, business: str, phone: str) -> int:
    return int(
        session.scalar(
            select(func.max(SourceMessage.delivery_revision)).where(
                SourceMessage.business_id == business,
                SourceMessage.phone_number == phone,
                visible_message_clause(),
            )
        )
        or 0
    )


def message_updates(
    session: Session, business: str, phone: str, after: int, limit: int
) -> tuple[list[SourceMessage], int, bool]:
    rows = session.scalars(
        select(SourceMessage)
        .where(
            SourceMessage.business_id == business,
            SourceMessage.phone_number == phone,
            SourceMessage.delivery_revision > max(after, 0),
            visible_message_clause(),
        )
        .order_by(SourceMessage.delivery_revision)
        .limit(min(max(limit, 1), 200) + 1)
    ).all()
    maximum = min(max(limit, 1), 200)
    items = list(rows[:maximum])
    return items, (items[-1].delivery_revision if items else max(after, 0)), len(rows) > maximum


def safe_attachments(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    values = metadata.get("attachments")
    if not isinstance(values, list):
        return []
    result = []
    for value in values[:20]:
        if not isinstance(value, dict):
            continue
        try:
            identifier = str(UUID(str(value.get("id"))))
        except ValueError:
            continue
        if value.get("kind") not in {"image", "audio", "document", "video", "sticker"}:
            continue
        item: dict[str, Any] = {"id": identifier, "kind": value["kind"]}
        for key in ("mime_type", "playback_mime_type"):
            mime = value.get(key)
            if isinstance(mime, str) and re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", mime):
                item[key] = mime
        filename = value.get("filename")
        if isinstance(filename, str) and all(ord(c) >= 32 for c in filename):
            item["filename"] = filename
        size = value.get("size_bytes")
        if type(size) is int and 0 <= size <= 100 * 1024 * 1024:
            item["size_bytes"] = size
        digest = value.get("sha256")
        if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest):
            item["sha256"] = digest
        caption = value.get("caption")
        if isinstance(caption, str) and len(caption) <= 4096:
            item["caption"] = caption
        if type(value.get("voice_note")) is bool:
            item["voice_note"] = value["voice_note"]
        item["availability"] = (
            value.get("availability")
            if value.get("availability") in {"ready", "unavailable", "pending"}
            else "unavailable"
        )
        error = value.get("error")
        if isinstance(error, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", error):
            item["error"] = error
        result.append(item)
    return result


def serialize_message(row: SourceMessage) -> dict[str, Any]:
    metadata = dict(row.message_metadata or {})
    attachments = safe_attachments(metadata)
    if "attachments" in metadata:
        metadata["attachments"] = attachments
    if row.delivery_status:
        metadata["delivery_status"] = row.delivery_status
    if row.provider_message_id:
        metadata["provider_message_id"] = row.provider_message_id
    if row.command_id:
        metadata["command_id"] = row.command_id
    return {
        "id": row.source_id,
        "role": row.role,
        "content": row.content,
        "created_at": row.created_at,
        "metadata": metadata,
        "event_key": row.event_key,
        "delivery_status": row.delivery_status,
        "command_id": row.command_id,
        "provider_message_id": row.provider_message_id,
        "attachments": attachments,
        "is_delivery_event": is_delivery_event(row),
    }
