import sys
import types
import unittest
import os
from unittest.mock import patch

class _Recognizer:
    def read(self, *_args, **_kwargs):
        return None

    def save(self, *_args, **_kwargs):
        return None


class _Scheduler:
    def add_job(self, *_args, **_kwargs):
        return None

    def start(self):
        return None

    def shutdown(self, *_args, **_kwargs):
        return None


class _NoopLimiter:
    def __init__(self, *_args, **_kwargs):
        pass

    def limit(self, *_args, **_kwargs):
        def decorator(func):
            return func

        return decorator


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

    def ilike(self, column, value):
        self.filters.append(("ilike", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, values))
        return self

    def execute(self):
        rows = self.rows
        for filter_item in self.filters:
            if filter_item[0] == "ilike":
                _, column, value = filter_item
                rows = [
                    row
                    for row in rows
                    if str(row.get(column, "")).lower() == str(value).lower()
                ]
            elif filter_item[0] == "in":
                _, column, values = filter_item
                allowed = {str(value).lower() for value in values}
                rows = [
                    row
                    for row in rows
                    if str(row.get(column, "")).lower() in allowed
                ]
        return _Response(rows)


class _Supabase:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        if name != "attendance":
            return _Query([])
        return _Query(self.rows)


def _install_import_stubs():
    cv2_stub = types.ModuleType("cv2")
    cv2_stub.face = types.SimpleNamespace(
        LBPHFaceRecognizer_create=lambda: _Recognizer()
    )
    cv2_stub.CascadeClassifier = lambda *_args, **_kwargs: object()
    sys.modules.setdefault("cv2", cv2_stub)

    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    alerts_stub = types.ModuleType("alerts")
    alerts_stub.init_mail = lambda *_args, **_kwargs: None
    alerts_stub.send_low_attendance_alert = lambda *_args, **_kwargs: []
    sys.modules.setdefault("alerts", alerts_stub)

    supabase_stub = types.ModuleType("supabase")
    supabase_stub.create_client = lambda *_args, **_kwargs: _Supabase([])
    sys.modules.setdefault("supabase", supabase_stub)

    scheduler_stub = types.ModuleType("apscheduler.schedulers.background")
    scheduler_stub.BackgroundScheduler = _Scheduler
    sys.modules.setdefault("apscheduler", types.ModuleType("apscheduler"))
    sys.modules.setdefault(
        "apscheduler.schedulers", types.ModuleType("apscheduler.schedulers")
    )
    sys.modules.setdefault("apscheduler.schedulers.background", scheduler_stub)

    limiter_stub = types.ModuleType("flask_limiter")
    limiter_stub.Limiter = _NoopLimiter
    sys.modules.setdefault("flask_limiter", limiter_stub)

    limiter_util_stub = types.ModuleType("flask_limiter.util")
    limiter_util_stub.get_remote_address = lambda: "127.0.0.1"
    sys.modules.setdefault("flask_limiter.util", limiter_util_stub)

    liveness_stub = types.ModuleType("liveness")
    liveness_stub.verify_liveness = lambda *_args, **_kwargs: True
    sys.modules.setdefault("liveness", liveness_stub)

    report_stub = types.ModuleType("report_engine")
    report_stub.generate_attendance_pdf = lambda *_args, **_kwargs: b"%PDF-1.4"
    sys.modules.setdefault("report_engine", report_stub)

os.environ["SECRET_KEY"] = "test-secret-key"
_install_import_stubs()
import app as attendance_app


class FacultyScopeTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"id": 1, "department": "CSE", "subject": "ai", "name": "A"},
            {"id": 2, "department": "CSE", "subject": "dbms", "name": "B"},
            {"id": 3, "department": "ECE", "subject": "ai", "name": "C"},
        ]
        attendance_app.supabase_client = _Supabase(self.rows)

    def test_faculty_scope_filters_attendance_by_department_and_subject(self):
        with attendance_app.app.test_request_context("/attendance"):
            attendance_app.session["role"] = "faculty"
            attendance_app.session["faculty_department"] = "cse"
            attendance_app.session["faculty_subjects"] = ["ai"]

            rows = attendance_app._fetch_scoped_attendance("*")

        self.assertEqual([row["id"] for row in rows], [1])

    def test_faculty_without_scope_gets_no_attendance_rows(self):
        with attendance_app.app.test_request_context("/attendance"):
            attendance_app.session["role"] = "faculty"
            attendance_app.session["faculty_department"] = ""
            attendance_app.session["faculty_subjects"] = []

            rows = attendance_app._fetch_scoped_attendance("*")

        self.assertEqual(rows, [])

    def test_admin_keeps_full_attendance_access(self):
        with attendance_app.app.test_request_context("/attendance"):
            attendance_app.session["role"] = "admin"

            rows = attendance_app._fetch_scoped_attendance("*")

        self.assertEqual([row["id"] for row in rows], [1, 2, 3])

    def test_delete_scope_allows_only_matching_records(self):
        scope = {"department": "cse", "subjects": ["ai"]}

        self.assertTrue(
            attendance_app._record_matches_faculty_scope(self.rows[0], scope)
        )
        self.assertFalse(
            attendance_app._record_matches_faculty_scope(self.rows[1], scope)
        )
        self.assertFalse(
            attendance_app._record_matches_faculty_scope(self.rows[2], scope)
        )

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
