import os
import sys
import unittest
from unittest.mock import patch
import app as attendance_app

os.environ["SECRET_KEY"] = "test-secret-key"
class SecretKeyTests(unittest.TestCase):
    def test_app_has_secret_key_set(self):
        self.assertIsNotNone(attendance_app.app.secret_key)

    def test_app_raises_when_secret_key_missing(self):
        with patch("os.getenv") as mock_getenv:
            mock_getenv.side_effect = lambda key, default=None: None if key == "SECRET_KEY" else default

            sys.modules.pop("app", None)

            with self.assertRaises(RuntimeError) as ctx:
                __import__("app")

            self.assertIn("SECRET_KEY is required", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
