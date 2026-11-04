"""Delivery facts update original messages without source-history reconciliation."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from crm_service.budget import ReadBudget
from crm_service.delivery import (
    delivery_watermark,
    is_delivery_event,
    merge_status,
    message_updates,
    serialize_message,
    visible_message_clause,
)
from crm_service.models import CRMConversation, DeliveryReceipt, SourceConversation, SourceMessage
from crm_service.sync import Synchronizer

PHONE = "same-synthetic-contact"


def source(identifier, role="asesor", *, phone=PHONE, metadata=None, content="SYNTHETIC reply", at=None):
    return {
        "id": identifier,
        "phone_number": phone,
        "role": role,
        "content": content,
        "metadata": metadata or {},
        "created_at": at or "2026-11-04T12:00:00+00:00",
        "event_key": f"synthetic-{identifier}-{phone}",
    }


def original(identifier=1, *, command="synthetic-command", provider="wamid.synthetic", **kwargs):
    return source(
        identifier,
        metadata={
            "source": "human",
            "source_event_id": command,
            "provider_message_id": provider,
            "delivery_status": "accepted",
        },
        **kwargs,
    )


def receipt(
    identifier, delivery="delivered", *, command="synthetic-command", provider="wamid.synthetic", **kwargs
):
    return source(
        identifier,
        "system",
        content=f"DELIVERY_STATUS: {delivery}",
        metadata={
            "source": "crm_delivery",
            "command_id": command,
            "provider_message_id": provider,
            "delivery_status": delivery,
        },
        **kwargs,
    )


def upsert(settings, sessions, *rows, business="tanaka"):
    worker = Synchronizer(settings, sessions)
    with sessions.begin() as session:
        for row in rows:
            worker.upsert_message(session, business, row)


@pytest.mark.parametrize(
    "old,new,expected",
    [
        (None, "unknown", "unknown"),
        ("accepted", "unknown", "accepted"),
        ("accepted", "delivered", "delivered"),
        ("delivered", "accepted", "delivered"),
        ("read", "delivered", "read"),
        ("read", "failed", "read"),
        ("delivered", "failed", "delivered"),
        ("accepted", "failed", "failed"),
        ("failed", "accepted", "failed"),
        ("failed", "delivered", "delivered"),
        ("unknown", "failed", "failed"),
        ("unknown", "unrecognized", "unknown"),
    ],
)
def test_delivery_evidence_is_monotonic_and_timeout_is_not_success_or_failure(old, new, expected):
    assert merge_status(old, new) == expected


def test_incremental_receipt_updates_original_and_keeps_raw_audit(settings, sessions):
    upsert(settings, sessions, original(), receipt(2), receipt(3, "read"))
    with sessions() as session:
        message = session.get(SourceMessage, ("tanaka", 1))
        assert serialize_message(message)["delivery_status"] == "read"
        assert serialize_message(message)["metadata"]["delivery_status"] == "read"
        assert message.message_metadata["delivery_status"] == "accepted", (
            "raw source fact must remain unchanged"
        )
        assert session.scalar(select(func.count()).select_from(DeliveryReceipt)) == 2
        raw = session.get(SourceMessage, ("tanaka", 3))
        assert raw.content == "DELIVERY_STATUS: read" and is_delivery_event(raw)
        assert session.scalar(select(func.count()).select_from(SourceMessage)) == 3
        assert session.scalars(select(SourceMessage).where(visible_message_clause())).all() == [message]


def test_duplicate_and_out_of_order_receipts_never_regress_or_repeat_updates(settings, sessions):
    upsert(settings, sessions, original(), receipt(2, "read"))
    with sessions() as session:
        checkpoint = delivery_watermark(session, "tanaka", PHONE)
    upsert(
        settings,
        sessions,
        receipt(2, "read"),
        receipt(3, "delivered"),
        receipt(4, "accepted"),
        receipt(5, "failed"),
    )
    with sessions() as session:
        assert session.get(SourceMessage, ("tanaka", 1)).delivery_status == "read"
        assert delivery_watermark(session, "tanaka", PHONE) == checkpoint
        assert session.scalar(select(func.count()).select_from(DeliveryReceipt)) == 4


def test_receipt_before_original_stays_pending_and_gets_fresh_crm_revision(settings, sessions):
    upsert(settings, sessions, receipt(50, "read"), original(100, command="other", provider="wamid.other"))
    with sessions() as session:
        after = delivery_watermark(session, "tanaka", PHONE)
        assert session.get(DeliveryReceipt, ("tanaka", 50)).matched_source_id is None
    # Deliberately use an older source ID, to exercise independent CRM revisions.
    upsert(settings, sessions, original(1))
    with sessions() as session:
        rows, cursor, more = message_updates(session, "tanaka", PHONE, after, 200)
        assert [row.source_id for row in rows] == [1]
        assert rows[0].delivery_status == "read" and cursor > after and not more
        assert session.get(DeliveryReceipt, ("tanaka", 50)).matched_source_id == 1


def test_old_message_outside_latest_page_updates_without_history_sweep(settings, sessions):
    upsert(
        settings,
        sessions,
        original(),
        *[source(identifier, "user", content="SYNTHETIC newer incoming") for identifier in range(2, 210)],
    )
    with sessions() as session:
        after = delivery_watermark(session, "tanaka", PHONE)
        latest = session.scalars(
            select(SourceMessage).order_by(SourceMessage.source_id.desc()).limit(100)
        ).all()
        assert 1 not in [row.source_id for row in latest]
    upsert(settings, sessions, receipt(210, "delivered"))
    with sessions() as session:
        rows, _, _ = message_updates(session, "tanaka", PHONE, after, 100)
        assert [row.source_id for row in rows] == [1]
        assert serialize_message(rows[0])["delivery_status"] == "delivered"


@pytest.mark.parametrize("other_scope", ["business", "conversation"])
def test_matching_ids_never_cross_scope(settings, sessions, other_scope):
    upsert(settings, sessions, original())
    upsert(
        settings,
        sessions,
        receipt(2, "read", phone="other" if other_scope == "conversation" else PHONE),
        business="memos" if other_scope == "business" else "tanaka",
    )
    with sessions() as session:
        assert session.get(SourceMessage, ("tanaka", 1)).delivery_status == "accepted"


def test_conflicting_identity_cannot_bind_by_text_or_time(settings, sessions):
    upsert(settings, sessions, original(), receipt(2, "read", provider="wamid.different"))
    with sessions() as session:
        assert session.get(SourceMessage, ("tanaka", 1)).delivery_status == "accepted"
        assert session.get(DeliveryReceipt, ("tanaka", 2)).matched_source_id is None
    upsert(settings, sessions, source(3, metadata={"delivery_status": "accepted"}))
    with sessions() as session:
        assert session.get(SourceMessage, ("tanaka", 3)).delivery_status == "accepted"


def test_provider_id_alone_can_match_but_ambiguous_identifiers_cannot(settings, sessions):
    first = original(command=None)
    upsert(settings, sessions, first, receipt(2, "delivered", command=None))
    with sessions() as session:
        assert session.get(SourceMessage, ("tanaka", 1)).delivery_status == "delivered"
    upsert(settings, sessions, original(3), original(4), receipt(5, "read"))
    with sessions() as session:
        assert session.get(DeliveryReceipt, ("tanaka", 5)).matched_source_id is None
        assert session.get(SourceMessage, ("tanaka", 3)).delivery_status == "accepted"


def test_stale_source_reconciliation_cannot_erase_delivered_evidence(settings, sessions):
    upsert(settings, sessions, original(), receipt(2, "read"), original())
    with sessions() as session:
        assert serialize_message(session.get(SourceMessage, ("tanaka", 1)))["delivery_status"] == "read"


def test_delivery_events_do_not_reopen_a_closed_cycle_but_handoff_and_close_remain_visible(
    settings, sessions
):
    upsert(
        settings,
        sessions,
        original(),
        source(2, "system", content="RESOLVED: SYNTHETIC case closed", at="2026-11-04T12:10:00+00:00"),
        receipt(3, "read", at="2026-11-04T12:20:00+00:00"),
    )
    worker = Synchronizer(settings, sessions)
    with sessions.begin() as session:
        source_row = session.get(SourceConversation, ("tanaka", PHONE))
        source_row.state = {"is_paused": False}
        worker.project(session, settings.businesses["tanaka"], [], False, ReadBudget(settings))
    with sessions() as session:
        assert session.get(CRMConversation, ("tanaka", PHONE)).bucket == "closed"
        assert not is_delivery_event(session.get(SourceMessage, ("tanaka", 2)))


def test_update_pagination_is_scoped_and_resumable(settings, sessions):
    upsert(
        settings,
        sessions,
        original(1, command="one", provider="wamid.one"),
        original(2, command="two", provider="wamid.two"),
    )
    with sessions() as session:
        first, cursor, more = message_updates(session, "tanaka", PHONE, 0, 1)
        assert len(first) == 1 and more
        second, checkpoint, more = message_updates(session, "tanaka", PHONE, cursor, 1)
        assert len(second) == 1 and not more and second[0].source_id != first[0].source_id
        rows, unchanged, _ = message_updates(session, "tanaka", PHONE, checkpoint, 1)
        assert rows == [] and unchanged == checkpoint


def test_serializer_drops_storage_credentials_from_attachments():
    row = SourceMessage(
        business_id="synthetic",
        source_id=1,
        phone_number=PHONE,
        role="user",
        content="",
        created_at=datetime.now(timezone.utc),
        message_metadata={
            "attachments": [
                {
                    "id": "7c1002c2-9006-4450-a412-60d430030533",
                    "kind": "audio",
                    "filename": "synthetic.ogg",
                    "mime_type": "audio/ogg",
                    "size_bytes": 20,
                    "availability": "ready",
                    "storage_key": "private",
                    "meta_token": "private",
                    "download_url": "https://private.invalid?token=private",
                }
            ]
        },
        is_delivery_event=False,
    )
    attachment = serialize_message(row)["attachments"][0]
    assert attachment["kind"] == "audio"
    assert not ({"storage_key", "meta_token", "download_url"} & attachment.keys())
