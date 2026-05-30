# tests/conftest.py
import sys
from unittest.mock import MagicMock

# Block mediapipe-dependent liveness module
mock_liveness = MagicMock()
mock_liveness.verify_liveness.return_value = True
sys.modules["liveness"] = mock_liveness

# Block supabase from connecting — no .env available in test environment
mock_supabase = MagicMock()
mock_supabase.create_client.return_value = MagicMock()
sys.modules["supabase"] = mock_supabase

# Block cv2 face recognizer attribute errors if opencv-contrib is missing
import unittest.mock
sys.modules.setdefault("cv2", unittest.mock.MagicMock())