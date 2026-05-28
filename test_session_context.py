import sys
import types
import unittest


class _FakeRecognizer:
    def read(self, *_args, **_kwargs):
        return None


class _FakeFace:
    @staticmethod
    def LBPHFaceRecognizer_create():
        return _FakeRecognizer()


class _FakeCascade:
    def __init__(self, *_args, **_kwargs):
        pass


cv2_stub = types.SimpleNamespace(
    face=_FakeFace(),
    CascadeClassifier=_FakeCascade,
    imread=lambda *_args, **_kwargs: None,
    cvtColor=lambda image, _code: image,
    resize=lambda image, _size: image,
    imdecode=lambda *_args, **_kwargs: None,
    imwrite=lambda *_args, **_kwargs: True,
    COLOR_BGR2GRAY=0,
    IMREAD_COLOR=1,
)


class _FakeSupabase:
    def table(self, *_args, **_kwargs):
        return self

    def select(self, *_args, **_kwargs):
        return self

    def execute(self):
        return types.SimpleNamespace(data=[])


sys.modules.setdefault("cv2", cv2_stub)
sys.modules.setdefault("supabase", types.SimpleNamespace(create_client=lambda *_args, **_kwargs: _FakeSupabase()))
sys.modules.setdefault("alerts", types.SimpleNamespace(init_mail=lambda *_args, **_kwargs: None, send_low_attendance_alert=lambda *_args, **_kwargs: []))
sys.modules.setdefault("apscheduler", types.ModuleType("apscheduler"))
sys.modules.setdefault("apscheduler.schedulers", types.ModuleType("apscheduler.schedulers"))
sys.modules.setdefault("apscheduler.schedulers.background", types.SimpleNamespace(BackgroundScheduler=lambda: types.SimpleNamespace(start=lambda: None, add_job=lambda **_kwargs: None, shutdown=lambda **_kwargs: None)))
sys.modules.setdefault("flask_limiter", types.SimpleNamespace(Limiter=lambda *args, **kwargs: types.SimpleNamespace(limit=lambda *_args, **_kwargs: (lambda fn: fn))))
sys.modules.setdefault("flask_limiter.util", types.SimpleNamespace(get_remote_address=lambda: "127.0.0.1"))
sys.modules.setdefault("liveness", types.SimpleNamespace(verify_liveness=lambda *_args, **_kwargs: True))

import app as attend_app


class ClassSessionContextTest(unittest.TestCase):
    def setUp(self):
        attend_app.app.config.update(TESTING=True, SECRET_KEY="test")

    def login_faculty(self, client, name, department="cse", subjects=None):
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["role"] = "faculty"
            sess["name"] = name
            sess["faculty_department"] = department
            sess["faculty_subjects"] = subjects or ["math"]

    def test_two_clients_keep_independent_class_contexts(self):
        faculty_a = attend_app.app.test_client()
        faculty_b = attend_app.app.test_client()
        self.login_faculty(faculty_a, "Faculty A", "cse", ["math"])
        self.login_faculty(faculty_b, "Faculty B", "ece", ["physics"])

        faculty_a.post("/session", data={"subject": "Math", "department": "CSE"})
        faculty_b.post("/session", data={"subject": "Physics", "department": "ECE"})

        page_a = faculty_a.get("/camera")
        page_b = faculty_b.get("/camera")

        self.assertEqual(page_a.status_code, 200)
        self.assertEqual(page_b.status_code, 200)
        self.assertIn(b'value="math"', page_a.data)
        self.assertIn(b'value="cse"', page_a.data)
        self.assertIn(b'value="physics"', page_b.data)
        self.assertIn(b'value="ece"', page_b.data)
        self.assertNotIn(b'value="physics"', page_a.data)
        self.assertNotIn(b'value="math"', page_b.data)

    def test_mark_attendance_context_prefers_request_payload(self):
        client = attend_app.app.test_client()
        self.login_faculty(client, "Faculty", "ece", ["physics"])

        with client:
            client.get("/camera?subject=math&department=cse")
            context = attend_app.get_class_session_context({"subject": "physics", "department": "ece"})

        self.assertEqual(context, {"subject": "physics", "department": "ece"})


if __name__ == "__main__":
    unittest.main()
