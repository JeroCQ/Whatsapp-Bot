from message_metrics import correlated_response_metrics, correlated_response_latencies


def row(source, *, event=None, replies=(), business="memos", key=None):
    return {"phone_number": "57300", "event_key": key, "metadata": {"source": source,
        "source_event_id": event, "inbound_message_ids": list(replies), "business_id": business}}


def test_one_reply_serves_multiple_incoming_but_followups_and_notes_do_not():
    rows = [row("incoming", event="a"), row("incoming", event="b"), row("incoming", event="c"),
            row("bot_response", replies=["a", "b"], key="reply"), row("bot_response", replies=["a", "b"], key="reply"),
            row("follow_up", replies=["c"]), row("internal_alert", replies=["c"])]
    metrics = correlated_response_metrics(rows)
    assert metrics["incoming_answered"] == 2
    assert metrics["incoming_without_linked_reply"] == 1
    assert metrics["message_kinds"]["bot_response"] == 1


def test_human_replies_and_cross_brand_ids_are_separate():
    metrics = correlated_response_metrics([row("incoming", event="a"), row("human", replies=["a"]),
        row("incoming", event="a", business="velvet"), {"role": "model", "content": "legacy"}])
    assert metrics["incoming_answered"] == 1
    assert metrics["incoming_without_linked_reply"] == 1
    assert metrics["legacy_unclassified_messages"] == 1


def test_latency_counts_a_batch_without_using_followup_or_time_proximity():
    rows = [row("incoming", event="a"), row("incoming", event="b"), row("follow_up", replies=["a", "b"]),
        row("bot_response", replies=["a", "b"])]
    for index, item in enumerate(rows):
        item["created_at"] = f"2026-10-21T15:00:0{index}Z"
    assert correlated_response_latencies(rows) == ([3.0, 2.0], [3.0, 2.0])
