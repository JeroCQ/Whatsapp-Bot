import unittest
from datetime import timedelta
from unittest.mock import patch

from queue_client import enqueue_in, follow_up_delay_seconds, web_queue_mode


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
        self.assertEqual(follow_up_delay_seconds(120, {}), 7200)

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


if __name__ == "__main__":
    unittest.main()
