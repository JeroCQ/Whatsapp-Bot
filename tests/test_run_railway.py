import unittest
from unittest.mock import patch

import run_railway
from run_railway import should_run_embedded_worker


class EmbeddedWorkerConfigurationTests(unittest.TestCase):
    def test_starts_embedded_worker_by_default(self):
        self.assertTrue(should_run_embedded_worker({"REDIS_URL": "redis://example"}))

    def test_explicit_true_keeps_worker_enabled(self):
        environment = {"REDIS_URL": "redis://example", "RUN_WORKER_IN_WEB": "true"}
        self.assertTrue(should_run_embedded_worker(environment))

    def test_can_be_disabled_for_a_verified_dedicated_worker(self):
        environment = {"REDIS_URL": "redis://example", "RUN_WORKER_IN_WEB": "false"}
        self.assertFalse(should_run_embedded_worker(environment, external_workers_seen=1))

    def test_starts_fallback_when_external_worker_is_missing(self):
        environment = {"REDIS_URL": "redis://example", "RUN_WORKER_IN_WEB": "false"}
        self.assertTrue(should_run_embedded_worker(environment, external_workers_seen=0))

    def test_never_starts_without_redis(self):
        self.assertFalse(should_run_embedded_worker({"RUN_WORKER_IN_WEB": "true"}))

    @patch("run_railway.signal.signal")
    @patch("run_railway.uvicorn.run")
    def test_uses_upload_safe_keep_alive_default(self, uvicorn_run, _signal):
        with patch.dict("run_railway.os.environ", {"PORT": "8080"}, clear=True):
            run_railway.main()

        self.assertEqual(uvicorn_run.call_args.kwargs["timeout_keep_alive"], 120)


if __name__ == "__main__":
    unittest.main()
