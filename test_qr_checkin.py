import sys
import types
import unittest
import os
from datetime import datetime, timedelta

class _FakeRecognizer:
    def read(self, *args, **kwargs):
        return None

class _FakeFace:
    @staticmethod
    def LBPHFaceRecognizer_create():
        return _FakeRecognizer()

class _FakeCascade:
    def __init__(self, *args, **kwargs):
        pass

cv2_stub = types.SimpleNamespace(
    face=_FakeFace(),
    CascadeClassifier=_FakeCascade,
    imread=lambda *args, **kwargs: None,
    cvtColor=lambda image, code: image,
    resize=lambda image, size: image,
    imdecode=lambda *args, **kwargs: None,
    imwrite=lambda *args, **kwargs: True,
    COLOR_BGR2GRAY=0,
    IMREAD_COLOR=1,
)

class _Response:
    def __init__(self, data=None):
        self.data = data or []

class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.inserted = []

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
        self.inserted.append(record)
        self.rows.append(record)
        return self

    def execute(self):
        rows = self.rows
        for filter_item in self.filters:
            if filter_item[0] == "eq":
                _, column, value = filter_item
                rows = [row for row in rows if str(row.get(column)).lower() == str(value).lower()]
            elif filter_item[0] == "ilike":
                _, column, value = filter_item
                rows = [row for row in rows if str(row.get(column, "")).lower() == str(value).lower()]
        return _Response(rows)

class _FakeSupabase:
    def __init__(self):
        self.students = [
            {"id": "s1", "name": "Alice Student", "reg_no": "STU001", "department": "CSE", "class": "A", "password": "hash"},
        ]
        self.attendance = []

    def table(self, name):
        if name == "students":
            return _Query(self.students)
        elif name == "attendance":
            return _Query(self.attendance)
        elif name == "faculty":
            return _Query([
                {"faculty_id": "prof_alice", "name": "Professor Alice", "department": "cse", "subjects": "math,ai", "password": "hash"}
            ])
        return _Query([])

sys.modules.setdefault("cv2", cv2_stub)
sys.modules.setdefault("supabase", types.SimpleNamespace(create_client=lambda *args, **kwargs: _FakeSupabase()))
sys.modules.setdefault("alerts", types.SimpleNamespace(init_mail=lambda *args, **kwargs: None, send_low_attendance_alert=lambda *args, **kwargs: []))
sys.modules.setdefault("apscheduler", types.ModuleType("apscheduler"))
sys.modules.setdefault("apscheduler.schedulers", types.ModuleType("apscheduler.schedulers"))
sys.modules.setdefault("apscheduler.schedulers.background", types.SimpleNamespace(BackgroundScheduler=lambda: types.SimpleNamespace(start=lambda: None, add_job=lambda **kwargs: None, shutdown=lambda **kwargs: None)))
sys.modules.setdefault("flask_limiter", types.SimpleNamespace(Limiter=lambda *args, **kwargs: types.SimpleNamespace(limit=lambda *args, **kwargs: (lambda fn: fn))))
sys.modules.setdefault("flask_limiter.util", types.SimpleNamespace(get_remote_address=lambda: "127.0.0.1"))
sys.modules.setdefault("liveness", types.SimpleNamespace(verify_liveness=lambda *args, **kwargs: True))
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import app as attend_app

class QRCheckinTest(unittest.TestCase):
    def setUp(self):
        attend_app.app.config.update(TESTING=True, SECRET_KEY="test")
        attend_app._qr_sessions.clear()
        self.client = attend_app.app.test_client()
        attend_app.supabase_client = _FakeSupabase()
        self._previous_public_base_url = os.environ.get("PUBLIC_BASE_URL")
        os.environ["PUBLIC_BASE_URL"] = "https://attendai.test/"

    def tearDown(self):
        if self._previous_public_base_url is None:
            os.environ.pop("PUBLIC_BASE_URL", None)
        else:
            os.environ["PUBLIC_BASE_URL"] = self._previous_public_base_url

    def login_user(self, role, username, department=None, subjects=None):
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["role"] = role
            sess["name"] = username
            if role == "student":
                sess["reg_no"] = username
            elif role == "faculty":
                sess["faculty_id"] = username
                sess["faculty_department"] = department or "cse"
                sess["faculty_subjects"] = subjects or ["math"]

    def test_generate_qr_denied_for_student(self):
        self.login_user("student", "STU001")
        response = self.client.get("/generate_qr")
        self.assertEqual(response.status_code, 403)

        response = self.client.post("/generate_qr", json={"subject": "math", "department": "cse"})
        self.assertEqual(response.status_code, 403)

    def test_generate_qr_success_for_faculty(self):
        self.login_user("faculty", "prof_alice", department="cse", subjects=["math"])
        response = self.client.post("/generate_qr", json={"subject": "math", "department": "cse"})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertIn("qr_image", data)
        self.assertIn("token", data)
        self.assertEqual(data["subject"], "math")

        token = data["token"]
        self.assertIn(token, attend_app._qr_sessions)

    def test_checkin_flow_success(self):
        # Generate token first
        self.login_user("faculty", "prof_alice", department="cse", subjects=["math"])
        gen_res = self.client.post("/generate_qr", json={"subject": "math", "department": "cse"})
        token = gen_res.get_json()["token"]

        self.client.get("/logout")
        self.login_user("student", "STU001")

        preview = self.client.get(f"/checkin/{token}")
        self.assertEqual(preview.status_code, 200)
        self.assertIn(b"Confirm Check-In", preview.data)

        with self.client.session_transaction() as sess:
            csrf_token = sess["qr_checkin_csrf_token"]

        response = self.client.post(f"/checkin/{token}", data={"csrf_token": csrf_token})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Checked In!", response.data)
        self.assertIn(b"Alice Student", response.data)

        # Verify attendance record exists in DB
        db_records = attend_app.supabase_client.table("attendance").execute().data
        self.assertEqual(len(db_records), 1)
        self.assertEqual(db_records[0]["student_id"], "s1")
        self.assertEqual(db_records[0]["subject"], "math")

    def test_checkin_duplicate_prevention(self):
        # Generate token
        self.login_user("faculty", "prof_alice", department="cse", subjects=["math"])
        gen_res = self.client.post("/generate_qr", json={"subject": "math", "department": "cse"})
        token = gen_res.get_json()["token"]

        self.client.get("/logout")
        self.login_user("student", "STU001")

        # First checkin
        preview1 = self.client.get(f"/checkin/{token}")
        self.assertIn(b"Confirm Check-In", preview1.data)
        with self.client.session_transaction() as sess:
            csrf1 = sess["qr_checkin_csrf_token"]
        res1 = self.client.post(f"/checkin/{token}", data={"csrf_token": csrf1})
        self.assertIn(b"Checked In!", res1.data)

        # Second checkin (duplicate)
        preview2 = self.client.get(f"/checkin/{token}")
        self.assertIn(b"Confirm Check-In", preview2.data)
        with self.client.session_transaction() as sess:
            csrf2 = sess["qr_checkin_csrf_token"]
        res2 = self.client.post(f"/checkin/{token}", data={"csrf_token": csrf2})
        self.assertIn(b"Already Checked In", res2.data)

        # Verify only one record in DB
        db_records = attend_app.supabase_client.table("attendance").execute().data
        self.assertEqual(len(db_records), 1)

    def test_checkin_expired_token(self):
        # Insert an expired token directly into session store
        expired_token = "expired-token-123"
        expires_at = datetime.now() - timedelta(minutes=1)
        attend_app._qr_sessions[expired_token] = {
            'subject': 'math',
            'department': 'cse',
            'faculty_id': 'prof_alice',
            'created_at': expires_at - timedelta(minutes=15),
            'expires_at': expires_at,
        }

        self.login_user("student", "STU001")
        response = self.client.get(f"/checkin/{expired_token}")
        self.assertIn(b"Check-in Failed", response.data)
        self.assertIn(b"expired", response.data.lower())

        # Verify it was removed from store
        self.assertNotIn(expired_token, attend_app._qr_sessions)

    def test_checkin_invalid_token(self):
        self.login_user("student", "STU001")
        response = self.client.get("/checkin/invalid-token")
        self.assertIn(b"Check-in Failed", response.data)
        self.assertIn(b"invalid", response.data.lower())

    def test_checkin_get_does_not_mark_attendance(self):
        self.login_user("faculty", "prof_alice", department="cse", subjects=["math"])
        gen_res = self.client.post("/generate_qr", json={"subject": "math", "department": "cse"})
        token = gen_res.get_json()["token"]

        self.client.get("/logout")
        self.login_user("student", "STU001")
        response = self.client.get(f"/checkin/{token}")
        self.assertIn(b"Confirm Check-In", response.data)

        db_records = attend_app.supabase_client.table("attendance").execute().data
        self.assertEqual(len(db_records), 0)

    def test_generate_qr_requires_public_base_url(self):
        os.environ.pop("PUBLIC_BASE_URL", None)
        self.login_user("faculty", "prof_alice", department="cse", subjects=["math"])
        response = self.client.post("/generate_qr", json={"subject": "math", "department": "cse"})
        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.get_json()["success"])

    def test_checkin_uses_supabase_retry_wrapper(self):
        self.login_user("faculty", "prof_alice", department="cse", subjects=["math"])
        gen_res = self.client.post("/generate_qr", json={"subject": "math", "department": "cse"})
        token = gen_res.get_json()["token"]

        self.client.get("/logout")
        self.login_user("student", "STU001")

        calls = []
        original_retry = attend_app.supabase_with_retry

        def _tracking_retry(operation_fn):
            calls.append(1)
            return operation_fn()

        attend_app.supabase_with_retry = _tracking_retry
        try:
            self.client.get(f"/checkin/{token}")
            with self.client.session_transaction() as sess:
                csrf_token = sess["qr_checkin_csrf_token"]
            self.client.post(f"/checkin/{token}", data={"csrf_token": csrf_token})
        finally:
            attend_app.supabase_with_retry = original_retry

        self.assertGreaterEqual(len(calls), 3)

if __name__ == "__main__":
    unittest.main()
