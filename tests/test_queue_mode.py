import unittest

from queue_client import web_queue_mode


class WebQueueModeTests(unittest.TestCase):
    def test_uses_background_tasks_without_redis(self):
        self.assertEqual(web_queue_mode({}), "background_tasks")

    def test_embedded_worker_is_the_redis_default(self):
        self.assertEqual(web_queue_mode({"REDIS_URL": "redis://example"}), "embedded_worker")

    def test_false_selects_external_worker(self):
        environment = {"REDIS_URL": "redis://example", "RUN_WORKER_IN_WEB": "false"}
        self.assertEqual(web_queue_mode(environment), "external_worker")


if __name__ == "__main__":
    unittest.main()
