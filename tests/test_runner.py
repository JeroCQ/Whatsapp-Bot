import os
import unittest
from unittest.mock import patch

from workers import runner


class RailwayRunnerTests(unittest.TestCase):
    def test_runner_starts_main_app_on_railway_port(self):
        with patch.dict(os.environ, {"PORT": "9123"}), patch.object(
            runner.uvicorn, "run"
        ) as run:
            runner.main()

        run.assert_called_once_with("main:app", host="0.0.0.0", port=9123)


if __name__ == "__main__":
    unittest.main()
