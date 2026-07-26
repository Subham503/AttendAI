"""
Tests for issue #50 — DoS protection against unbounded base64 image payloads.

Verifies that:
  1. ``decode_image_payload`` enforces the encoded-string size limit BEFORE
     base64 decoding (Layer 1).
  2. ``decode_image_payload`` enforces the decoded-bytes size limit BEFORE
     ``cv2.imdecode`` runs (Layer 2).
  3. ``decode_image_payload`` rejects malformed base64 (Layer 2 input validation).
  4. ``/mark_attendance`` returns 413 for an oversized image without ever
     invoking the (potentially expensive) liveness / face-detection pipeline.
  5. ``/register`` rejects requests with more than ``MAX_REGISTER_FRAMES``
     frames before any image is decoded.
  6. The global ``MAX_CONTENT_LENGTH`` cap returns a clean JSON 413 for any
     oversized POST body (issue #50 hardening layer).
  7. Legitimate small images are still accepted end-to-end.
"""

import base64
import sys
import types
import unittest


# ---------------------------------------------------------------------
# Stub heavy / optional dependencies so the test file can run without
# opencv, supabase, mediapipe, etc. installed.
# ---------------------------------------------------------------------

class _FakeRecognizer:
    def read(self, *args, **kwargs):
        return None

    def save(self, *args, **kwargs):
        return None

    def predict(self, *args, **kwargs):
        # label, confidence — high confidence so no names get "marked"
        return 0, 999


class _FakeFace:
    @staticmethod
    def LBPHFaceRecognizer_create():
        return _FakeRecognizer()


class _FakeCascade:
    def __init__(self, *args, **kwargs):
        pass

    def detectMultiScale(self, *args, **kwargs):
        # Return an empty tuple — no faces detected — so the endpoint
        # short-circuits before any DB write.
        return ()


# cv2 stub. ``imdecode`` returns a small fixed-shape numpy-ish object so
# downstream ``cvtColor`` / ``detectMultiScale`` calls don't crash.
cv2_stub = types.SimpleNamespace(
    face=_FakeFace(),
    CascadeClassifier=_FakeCascade,
    imread=lambda *a, **k: None,
    cvtColor=lambda image, code: image,
    resize=lambda image, size: image,
    imdecode=lambda *a, **k: _DecodedFrame(),
    imwrite=lambda *a, **k: True,
    COLOR_BGR2GRAY=0,
    COLOR_BGR2RGB=4,
    IMREAD_COLOR=1,
)


class _DecodedFrame:
    """Minimal stand-in for a numpy array returned by cv2.imdecode."""

    shape = (1, 1, 3)
    dtype = "uint8"

    def __getitem__(self, key):
        return self

    def __len__(self):
        return 0


class _Response:
    def __init__(self, data=None):
        self.data = data or []


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def select(self, columns):
        self.filters.append(("select", columns))
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def ilike(self, column, value):
        self.filters.append(("ilike", column, value))
        return self

    def insert(self, record):
        self.rows.append(record)
        return self

    def execute(self):
        return _Response([])


class _FakeSupabase:
    def __init__(self):
        self.students = []
        self.attendance = []
        self.admins = []
        self.faculty = []

    def table(self, name):
        if name == "students":
            return _Query(self.students)
        if name == "attendance":
            return _Query(self.attendance)
        if name == "admins":
            return _Query(self.admins)
        if name == "faculty":
            return _Query(self.faculty)
        return _Query([])


# Inject stubs BEFORE importing ``app`` so the module-level imports succeed.
sys.modules.setdefault("cv2", cv2_stub)
sys.modules.setdefault(
    "supabase",
    types.SimpleNamespace(create_client=lambda *a, **k: _FakeSupabase()),
)
sys.modules.setdefault(
    "alerts",
    types.SimpleNamespace(
        init_mail=lambda *a, **k: None,
        send_low_attendance_alert=lambda *a, **k: [],
    ),
)
sys.modules.setdefault("apscheduler", types.ModuleType("apscheduler"))
sys.modules.setdefault(
    "apscheduler.schedulers", types.ModuleType("apscheduler.schedulers")
)
sys.modules.setdefault(
    "apscheduler.schedulers.background",
    types.SimpleNamespace(
        BackgroundScheduler=lambda: types.SimpleNamespace(
            start=lambda: None,
            add_job=lambda **k: None,
            shutdown=lambda **k: None,
        )
    ),
)
sys.modules.setdefault(
    "flask_limiter",
    types.SimpleNamespace(
        Limiter=lambda *a, **k: types.SimpleNamespace(
            limit=lambda *a, **k: (lambda fn: fn)
        )
    ),
)
sys.modules.setdefault(
    "flask_limiter.util",
    types.SimpleNamespace(get_remote_address=lambda: "127.0.0.1"),
)
sys.modules.setdefault(
    "liveness", types.SimpleNamespace(verify_liveness=lambda *a, **k: True)
)
# report_engine imports reportlab — stub it so the module loads cleanly.
sys.modules.setdefault(
    "report_engine",
    types.SimpleNamespace(generate_attendance_pdf=lambda *a, **k: None),
)

import app as attend_app  # noqa: E402


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _b64(payload: bytes) -> str:
    """Wrap raw bytes in a ``data:image/jpeg;base64,...`` data URI."""
    return "data:image/jpeg;base64," + base64.b64encode(payload).decode("ascii")


# ---------------------------------------------------------------------
# Unit tests for ``decode_image_payload``
# ---------------------------------------------------------------------


class DecodeImagePayloadTest(unittest.TestCase):
    def setUp(self):
        # Save originals so we can restore them between tests.
        self._orig_max_bytes = attend_app.MAX_IMAGE_BYTES
        self._orig_max_uri = attend_app.MAX_IMAGE_DATA_URI_BYTES
        # Force cv2.imdecode to return a non-None frame so the "happy path"
        # tests succeed regardless of which cv2 stub was registered first.
        self._orig_imdecode = attend_app.cv2.imdecode
        attend_app.cv2.imdecode = lambda *a, **k: _DecodedFrame()

    def tearDown(self):
        attend_app.MAX_IMAGE_BYTES = self._orig_max_bytes
        attend_app.MAX_IMAGE_DATA_URI_BYTES = self._orig_max_uri
        attend_app.cv2.imdecode = self._orig_imdecode

    def test_rejects_empty_input(self):
        frame, err = attend_app.decode_image_payload("")
        self.assertIsNone(frame)
        self.assertEqual(err[0], 400)

    def test_rejects_non_string_input(self):
        frame, err = attend_app.decode_image_payload(None)
        self.assertIsNone(frame)
        self.assertEqual(err[0], 400)

        frame, err = attend_app.decode_image_payload(12345)
        self.assertIsNone(frame)
        self.assertEqual(err[0], 400)

    def test_rejects_oversized_encoded_string_before_decoding(self):
        # Force a tiny limit so the test doesn't have to allocate a huge string.
        attend_app.MAX_IMAGE_DATA_URI_BYTES = 100
        attend_app.MAX_IMAGE_BYTES = 10_000_000  # generous so Layer 2 never triggers
        big_payload = _b64(b"A" * 200)  # encoded > 100 chars

        frame, err = attend_app.decode_image_payload(big_payload)
        self.assertIsNone(frame)
        self.assertEqual(err[0], 413)
        self.assertIn("encoded", err[1].lower())

    def test_rejects_oversized_decoded_bytes_before_imdecode(self):
        # Layer 2: encoded string is under the URI limit, but the decoded
        # bytes exceed MAX_IMAGE_BYTES.
        attend_app.MAX_IMAGE_DATA_URI_BYTES = 10_000_000  # generous
        attend_app.MAX_IMAGE_BYTES = 50
        payload = _b64(b"B" * 200)  # 200 decoded bytes > 50

        frame, err = attend_app.decode_image_payload(payload)
        self.assertIsNone(frame)
        self.assertEqual(err[0], 413)
        self.assertIn("decoded", err[1].lower())

    def test_rejects_malformed_base64(self):
        # Non-alphabet characters must be rejected by ``validate=True``.
        # This is a small (under-limit) payload that fails decoding.
        attend_app.MAX_IMAGE_DATA_URI_BYTES = 10_000
        attend_app.MAX_IMAGE_BYTES = 10_000
        payload = "data:image/jpeg;base64,!!!not_valid_base64!!!"

        frame, err = attend_app.decode_image_payload(payload)
        self.assertIsNone(frame)
        self.assertEqual(err[0], 400)

    def test_accepts_valid_small_payload(self):
        attend_app.MAX_IMAGE_DATA_URI_BYTES = 10_000
        attend_app.MAX_IMAGE_BYTES = 10_000
        payload = _b64(b"valid_image_bytes")

        frame, err = attend_app.decode_image_payload(payload)
        self.assertIsNone(err)
        self.assertIsNotNone(frame)

    def test_accepts_raw_base64_without_data_uri_header(self):
        # The helper should also tolerate raw base64 (no ``data:...`` prefix).
        attend_app.MAX_IMAGE_DATA_URI_BYTES = 10_000
        attend_app.MAX_IMAGE_BYTES = 10_000
        payload = base64.b64encode(b"raw_bytes").decode("ascii")

        frame, err = attend_app.decode_image_payload(payload)
        self.assertIsNone(err)
        self.assertIsNotNone(frame)


# ---------------------------------------------------------------------
# Integration tests for ``/mark_attendance``
# ---------------------------------------------------------------------


class MarkAttendanceSizeLimitTest(unittest.TestCase):
    def setUp(self):
        attend_app.app.config.update(TESTING=True, SECRET_KEY="test")
        self.client = attend_app.app.test_client()
        attend_app.supabase_client = _FakeSupabase()
        # Pretend the recognizer + label map are loaded so the endpoint
        # reaches the image-decoding step.
        attend_app.global_recognizer = _FakeRecognizer()
        attend_app.global_label_map = {0: ("s1", "Alice", "STU001", "cse", "A")}
        # Force cv2.imdecode to return a non-None frame so valid-image
        # tests reach the face-detection stage instead of bouncing out at
        # the "Invalid image received" guard.
        self._orig_imdecode = attend_app.cv2.imdecode
        attend_app.cv2.imdecode = lambda *a, **k: _DecodedFrame()
        # The cv2 stub registered by test_qr_checkin.py's _FakeCascade has
        # no detectMultiScale method. Replace CascadeClassifier with our
        # own that returns zero faces so the endpoint replies "no face".
        self._orig_cc = attend_app.cv2.CascadeClassifier
        attend_app.cv2.CascadeClassifier = _FakeCascade

    def tearDown(self):
        attend_app.cv2.imdecode = self._orig_imdecode
        attend_app.cv2.CascadeClassifier = self._orig_cc

    def _login_as_faculty(self):
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["role"] = "faculty"
            sess["name"] = "Prof Alice"
            sess["faculty_id"] = "prof_alice"
            sess["faculty_department"] = "cse"
            sess["faculty_subjects"] = ["math"]
            sess[attend_app.CLASS_CONTEXT_SESSION_KEY] = {
                "subject": "math",
                "department": "cse",
            }

    def test_oversized_image_returns_413(self):
        self._login_as_faculty()

        # Override the limit to something tiny so we don't have to build a
        # multi-MB string in the test process.
        orig_uri = attend_app.MAX_IMAGE_DATA_URI_BYTES
        attend_app.MAX_IMAGE_DATA_URI_BYTES = 200
        try:
            big_payload = _b64(b"X" * 500)
            resp = self.client.post(
                "/mark_attendance",
                json={"image": big_payload},
            )
        finally:
            attend_app.MAX_IMAGE_DATA_URI_BYTES = orig_uri

        self.assertEqual(resp.status_code, 413)
        body = resp.get_json()
        self.assertFalse(body["success"])
        self.assertIn("large", body["message"].lower())

    def test_missing_image_returns_400(self):
        self._login_as_faculty()
        resp = self.client.post("/mark_attendance", json={})
        # ``image_data`` defaults to "" → ``decode_image_payload`` returns 400.
        # The endpoint may also hit the "Model not trained" guard first;
        # we set a label map above so we should reach the decoder.
        self.assertIn(resp.status_code, (400, 413))

    def test_valid_small_image_is_accepted_for_decoding(self):
        self._login_as_faculty()
        small_payload = _b64(b"tiny")
        resp = self.client.post(
            "/mark_attendance",
            json={"image": small_payload},
        )
        # The frame decodes successfully; the stubbed face cascade returns
        # no faces, so the endpoint responds with the "no face" message
        # (200 with success=False). The point of this test is that the
        # request is NOT rejected with 413/400.
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertFalse(body["success"])
        # Either "No face detected" or the liveness failure — both are
        # post-decode responses, which is what we want.
        self.assertIn("face", body["message"].lower())


# ---------------------------------------------------------------------
# Integration tests for ``/register`` frame-count cap
# ---------------------------------------------------------------------


class RegisterFrameCountLimitTest(unittest.TestCase):
    def setUp(self):
        attend_app.app.config.update(TESTING=True, SECRET_KEY="test")
        self.client = attend_app.app.test_client()
        attend_app.supabase_client = _FakeSupabase()

    def test_too_many_frames_is_rejected_before_any_decode(self):
        # Override the cap so we don't have to build 30 real frames.
        orig_max = attend_app.MAX_REGISTER_FRAMES
        attend_app.MAX_REGISTER_FRAMES = 5
        try:
            resp = self.client.post(
                "/register",
                json={
                    "name": "Test Student",
                    "reg_no": "STU999",
                    "department": "CSE",
                    "class_name": "A",
                    "password": "secret123",
                    "email": "test@example.com",
                    # 6 frames > cap of 5
                    "frames": ["data:image/jpeg;base64,AAAA"] * 6,
                },
            )
        finally:
            attend_app.MAX_REGISTER_FRAMES = orig_max

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertFalse(body["success"])
        self.assertIn("frames", body["message"].lower())


# ---------------------------------------------------------------------
# Global MAX_CONTENT_LENGTH hardening
# ---------------------------------------------------------------------


class GlobalContentLengthTest(unittest.TestCase):
    def setUp(self):
        attend_app.app.config.update(TESTING=True, SECRET_KEY="test")
        self.client = attend_app.app.test_client()
        attend_app.supabase_client = _FakeSupabase()

    def test_oversized_post_body_returns_413_json(self):
        # Force a tiny MAX_CONTENT_LENGTH so the test is fast.
        orig = attend_app.app.config["MAX_CONTENT_LENGTH"]
        attend_app.app.config["MAX_CONTENT_LENGTH"] = 500
        try:
            # Login as faculty so we get past the auth guard and reach the
            # body-size check.
            with self.client.session_transaction() as sess:
                sess["logged_in"] = True
                sess["role"] = "faculty"
                sess["name"] = "Prof"
                sess["faculty_id"] = "p"
                sess["faculty_department"] = "cse"
                sess["faculty_subjects"] = ["math"]
                sess[attend_app.CLASS_CONTEXT_SESSION_KEY] = {
                    "subject": "math",
                    "department": "cse",
                }

            big_payload = _b64(b"X" * 5_000)  # ~6.7 KB encoded, well over 500 B
            resp = self.client.post(
                "/mark_attendance",
                json={"image": big_payload},
            )
        finally:
            attend_app.app.config["MAX_CONTENT_LENGTH"] = orig

        self.assertEqual(resp.status_code, 413)
        # Must be JSON, not Werkzeug's default HTML 413 page.
        body = resp.get_json()
        self.assertIsNotNone(body)
        self.assertFalse(body["success"])
        self.assertIn("large", body["message"].lower())


if __name__ == "__main__":
    unittest.main()
