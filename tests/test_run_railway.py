import unittest

from run_railway import should_run_embedded_worker


class EmbeddedWorkerConfigurationTests(unittest.TestCase):
    def test_starts_embedded_worker_by_default(self):
        self.assertTrue(should_run_embedded_worker({"REDIS_URL": "redis://example"}))

    def test_explicit_true_keeps_worker_enabled(self):
        environment = {"REDIS_URL": "redis://example", "RUN_WORKER_IN_WEB": "true"}
        self.assertTrue(should_run_embedded_worker(environment))

    def test_can_be_disabled_for_a_verified_dedicated_worker(self):
        environment = {"REDIS_URL": "redis://example", "RUN_WORKER_IN_WEB": "false"}
        self.assertFalse(should_run_embedded_worker(environment))

    def test_never_starts_without_redis(self):
        self.assertFalse(should_run_embedded_worker({"RUN_WORKER_IN_WEB": "true"}))


if __name__ == "__main__":
    unittest.main()
