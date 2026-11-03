from datetime import datetime, timedelta, timezone

import pytest

from crm_service.classification import classify

AT = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def msg(i, role, text="hello", metadata=None, seconds=None):
    return {
        "id": i,
        "role": role,
        "content": text,
        "created_at": (AT + timedelta(seconds=i if seconds is None else seconds)).isoformat(),
        "metadata": metadata or {},
    }


def classify_messages(messages, paused=True, chatwoot=44, handoff=None):
    return classify(
        {"is_paused": paused, "chatwoot_conversation_id": chatwoot, "last_interaction": AT.isoformat()},
        messages,
        handoff or {},
        AT + timedelta(hours=1),
    )


def test_resolved_then_customer_reopens():
    result = classify_messages([msg(1, "system", "RESOLVED: done"), msg(2, "user")], paused=False)
    assert result["bucket"] == "ai_active"


def test_old_handoff_and_response_do_not_cross_cycles():
    messages = [
        msg(1, "system", "HANDOFF: old"),
        msg(2, "asesor"),
        msg(3, "system", "RESOLVED: closed"),
        msg(4, "user"),
        msg(5, "system", "HANDOFF: new"),
    ]
    assert classify_messages(messages)["bucket"] == "awaiting_human"


@pytest.mark.parametrize(
    "paused,roles,bucket",
    [
        (True, ["user", "system", "asesor", "user"], "awaiting_human"),
        (True, ["user", "system", "asesor"], "human_active"),
        (False, ["user", "system", "asesor"], "ai_active"),
    ],
)
def test_response_order(paused, roles, bucket):
    messages = [
        msg(i, role, "HANDOFF: current" if role == "system" else "hi") for i, role in enumerate(roles, 1)
    ]
    assert classify_messages(messages, paused=paused)["bucket"] == bucket


def test_paused_without_chatwoot():
    result = classify_messages([msg(1, "asesor")], chatwoot=None)
    assert result["bucket"] == "awaiting_human"
    assert result["priority"] == 2


@pytest.mark.parametrize(
    "status,priority", [("unknown", 1), ("exhausted", 1), ("pending", 3), ("reserved", 3), ("sending", 3)]
)
def test_delivery_status_forces_attention(status, priority):
    result = classify_messages(
        [msg(1, "user"), msg(3, "asesor")],
        handoff={"status": status, "created_at": (AT + timedelta(seconds=2)).isoformat()},
    )
    assert result["bucket"] == "awaiting_human"
    assert result["priority"] == priority


def test_historical_delivery_does_not_override_current_cycle():
    result = classify_messages(
        [
            msg(1, "system", "HANDOFF: old"),
            msg(3, "system", "RESOLVED: done"),
            msg(4, "user"),
            msg(5, "asesor"),
        ],
        handoff={"status": "unknown", "created_at": AT.isoformat()},
    )
    assert result["bucket"] == "human_active"


def test_reused_handoff_row_uses_updated_at_for_current_request():
    messages = [msg(1, "system", "RESOLVED: old"), msg(2, "user"), msg(3, "asesor")]
    result = classify_messages(
        messages,
        handoff={
            "status": "unknown",
            "created_at": AT.isoformat(),
            "updated_at": (AT + timedelta(seconds=4)).isoformat(),
        },
    )
    assert result["bucket"] == "awaiting_human" and result["priority"] == 1
    assert "handoff_cycle_inferred_from_updated_at" in result["data_quality_warnings"]


def test_unpaused_history_is_not_handoff():
    assert (
        classify_messages(
            [msg(1, "system", "HANDOFF: old"), msg(2, "user")], paused=False, handoff={"status": "unknown"}
        )["bucket"]
        == "ai_active"
    )


def test_closed_current_cycle():
    assert (
        classify_messages([msg(1, "user"), msg(2, "system", "RESOLVED: done")], paused=False)["bucket"]
        == "closed"
    )


@pytest.mark.parametrize(
    "role,content", [("system", "HANDOFF: new cycle"), ("model", "new activity"), ("asesor", "new activity")]
)
def test_activity_after_close_is_a_new_cycle(role, content):
    result = classify_messages([msg(1, "system", "RESOLVED: done"), msg(2, role, content)], paused=False)
    assert result["bucket"] == "ai_active"


def test_modern_metadata_precedes_timestamp():
    messages = [
        msg(1, "user", metadata={"source_event_id": "a"}),
        msg(2, "asesor", metadata={"inbound_message_ids": ["b"]}),
    ]
    result = classify_messages(messages)
    assert result["unanswered_customer_count"] == 1
    assert result["bucket"] == "awaiting_human"
    messages[1]["metadata"]["inbound_message_ids"] = ["a"]
    assert classify_messages(messages)["bucket"] == "human_active"


def test_modern_response_answers_clock_skewed_incoming():
    messages = [
        msg(1, "asesor", metadata={"inbound_message_ids": ["incoming"]}),
        msg(2, "user", metadata={"source_event_id": "incoming"}),
    ]
    assert classify_messages(messages)["unanswered_customer_count"] == 0


def test_legacy_same_timestamp_uses_id():
    messages = [msg(2, "asesor", seconds=0), msg(1, "user", seconds=0)]
    result = classify_messages(messages)
    assert result["bucket"] == "human_active"
    assert "legacy_response_order_fallback" in result["data_quality_warnings"]


def test_attention_is_oldest_unanswered_and_priority_stable():
    messages = [msg(1, "user"), msg(2, "asesor"), msg(3, "user"), msg(4, "user")]
    result = classify_messages(messages)
    assert result["attention_since"] == messages[2]["created_at"]
    assert result["waiting_seconds"] == 3597
    assert result["unanswered_customer_count"] == 2


def test_missing_state_fails_conservatively():
    result = classify({}, [msg(1, "user")], {}, AT)
    assert result["bucket"] == "awaiting_human"
    assert "missing_is_paused_conservative_attention" in result["data_quality_warnings"]


def test_function_does_not_mutate_inputs():
    messages = [msg(2, "user"), msg(1, "system", "RESOLVED: old")]
    before = repr(messages)
    classify_messages(messages)
    assert repr(messages) == before


def receipt(i, command, status):
    return msg(
        i,
        "system",
        "DELIVERY_STATUS: " + status,
        metadata={
            "source": "crm_delivery",
            "command_id": command,
            "delivery_status": status,
        },
    )


@pytest.mark.parametrize("modern", [True, False])
def test_failed_human_response_does_not_answer_customer(modern):
    incoming = {"source_event_id": "inbound-one"} if modern else {}
    human = {"delivery_status": "failed"}
    if modern:
        human.update(source_event_id="reply-one", inbound_message_ids=["inbound-one"])
    result = classify_messages([msg(1, "user", metadata=incoming), msg(2, "asesor", metadata=human)])
    assert result["bucket"] == "awaiting_human"
    assert result["unanswered_customer_count"] == 1


def test_incremental_failed_receipt_overrides_cached_accepted_response_without_mutation():
    messages = [
        msg(1, "user", metadata={"source_event_id": "inbound-one"}),
        msg(
            2,
            "asesor",
            metadata={
                "source_event_id": "reply-one",
                "inbound_message_ids": ["inbound-one"],
                "delivery_status": "accepted",
            },
        ),
        receipt(3, "reply-one", "failed"),
    ]
    before = repr(messages)
    result = classify_messages(messages)
    assert result["bucket"] == "awaiting_human" and result["unanswered_customer_count"] == 1
    assert result["last_message_id"] == 2
    assert repr(messages) == before


def test_receipt_for_different_command_does_not_invalidate_human_response():
    messages = [
        msg(1, "user", metadata={"source_event_id": "inbound-one"}),
        msg(
            2,
            "asesor",
            metadata={
                "source_event_id": "reply-one",
                "inbound_message_ids": ["inbound-one"],
                "delivery_status": "accepted",
            },
        ),
        receipt(3, "other-reply", "failed"),
    ]
    assert classify_messages(messages)["bucket"] == "human_active"


@pytest.mark.parametrize("status", ["accepted", "delivered", "read", "failed"])
def test_receipts_after_close_do_not_reopen_or_create_unread_activity(status):
    messages = [msg(1, "user"), msg(2, "system", "RESOLVED: done"), receipt(3, "old-reply", status)]
    result = classify_messages(messages, paused=False)
    assert result["bucket"] == "closed"
    assert result["last_message_id"] == 2


def test_receipts_and_reconciled_metadata_preserve_monotonic_delivery():
    messages = [
        msg(1, "user", metadata={"source_event_id": "inbound-one"}),
        msg(
            2,
            "asesor",
            metadata={
                "source_event_id": "reply-one",
                "inbound_message_ids": ["inbound-one"],
                "delivery_status": "failed",
            },
        ),
        receipt(3, "reply-one", "accepted"),
    ]
    assert classify_messages(messages)["bucket"] == "awaiting_human"
    messages.extend([receipt(4, "reply-one", "delivered"), receipt(5, "reply-one", "accepted")])
    assert classify_messages(messages)["bucket"] == "human_active"
