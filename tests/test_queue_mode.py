import unittest
import ast
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from queue_client import (
    claim_follow_up,
    enqueue_in,
    follow_up_delay_seconds,
    invalidate_follow_up,
    register_follow_up,
    web_queue_mode,
)


def test_follow_up_delay_helper_has_one_definition():
    source = Path("queue_client.py").read_text(encoding="utf-8")
    assert source.count("def follow_up_delay_seconds(") == 1


def test_provider_webhook_request_paths_do_not_call_remote_idempotency_or_redis():
    """Meta/Chatwoot must ACK before any Supabase write or Redis enqueue."""
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for endpoint in ("receive_webhook", "chatwoot_webhook"):
        calls = {
            node.func.id
            for node in ast.walk(functions[endpoint])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "claim_webhook_event" not in calls
        assert "enqueue" not in calls

    assert any(
        isinstance(node, ast.Attribute)
        and node.attr == "add_task"
        for node in ast.walk(functions["receive_webhook"])
    )
    assert any(
        isinstance(node, ast.Attribute)
        and node.attr == "add_task"
        for node in ast.walk(functions["chatwoot_webhook"])
    )


class WebQueueModeTests(unittest.TestCase):
    def test_uses_background_tasks_without_redis(self):
        self.assertEqual(web_queue_mode({}), "background_tasks")

    def test_embedded_worker_is_the_redis_default(self):
        self.assertEqual(web_queue_mode({"REDIS_URL": "redis://example"}), "embedded_worker")

    def test_false_selects_external_worker(self):
        environment = {"REDIS_URL": "redis://example", "RUN_WORKER_IN_WEB": "false"}
        self.assertEqual(web_queue_mode(environment), "external_worker")


class FollowUpDelayTests(unittest.TestCase):
    def test_converts_model_minutes_to_seconds(self):
        now = datetime(2026, 8, 25, 10, 0, tzinfo=ZoneInfo("America/Bogota"))
        self.assertEqual(follow_up_delay_seconds(120, {}, now), 7200)

    def test_moves_evening_follow_up_to_next_day_at_eight(self):
        now = datetime(2026, 8, 28, 17, 0, tzinfo=ZoneInfo("America/Bogota"))
        self.assertEqual(follow_up_delay_seconds(120, {}, now), 15 * 60 * 60)

    def test_moves_early_follow_up_to_same_day_at_eight(self):
        now = datetime(2026, 8, 25, 5, 0, tzinfo=ZoneInfo("America/Bogota"))
        self.assertEqual(follow_up_delay_seconds(60, {}, now), 3 * 60 * 60)

    def test_test_override_avoids_waiting_two_hours(self):
        environment = {"FOLLOW_UP_TEST_DELAY_SECONDS": "5"}
        self.assertEqual(follow_up_delay_seconds(120, environment), 5)

    def test_test_override_never_allows_zero_delay(self):
        environment = {"FOLLOW_UP_TEST_DELAY_SECONDS": "0"}
        self.assertEqual(follow_up_delay_seconds(120, environment), 1)

    def test_enqueue_in_passes_exact_delay_to_rq(self):
        class FakeQueue:
            def __init__(self):
                self.call = None

            def enqueue_in(self, *args, **kwargs):
                self.call = (args, kwargs)
                return "scheduled-job"

        queue = FakeQueue()
        callback = lambda: None
        with patch("queue_client.get_queue", return_value=queue):
            result = enqueue_in(5, callback, "phone", job_id="follow-up:test")

        self.assertEqual(result, "scheduled-job")
        self.assertEqual(queue.call[0][:3], (timedelta(seconds=5), callback, "phone"))
        self.assertEqual(queue.call[1]["job_id"], "follow-up_test")

    def test_redis_token_can_be_registered_cancelled_and_claimed(self):
        class FakeRedis:
            def __init__(self):
                self.values = {}

            def set(self, key, value, ex=None):
                self.values[key] = value

            def delete(self, key):
                return int(self.values.pop(key, None) is not None)

            def eval(self, script, key_count, key, token):
                if self.values.get(key) != token:
                    return 0
                return self.delete(key)

        class FakeQueue:
            connection = FakeRedis()

        queue = FakeQueue()
        with patch("queue_client.get_queue", return_value=queue):
            first_token = register_follow_up("57300", 10)
            self.assertTrue(claim_follow_up("57300", first_token))
            self.assertFalse(claim_follow_up("57300", first_token))

            second_token = register_follow_up("57300", 10)
            invalidate_follow_up("57300")
            self.assertFalse(claim_follow_up("57300", second_token))


if __name__ == "__main__":
    unittest.main()
