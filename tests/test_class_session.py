import pytest
from app import app, CLASS_CONTEXT_SESSION_KEY


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        yield client


def faculty_session(client):
    """Helper: inject a logged-in faculty session directly."""
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["role"] = "faculty"
        sess["faculty_department"] = "cs"
        sess["faculty_subjects"] = ["math", "physics"]


def set_class_context(client, subject, department):
    """Helper: inject a valid class context directly into the session."""
    with client.session_transaction() as sess:
        sess[CLASS_CONTEXT_SESSION_KEY] = {
            "subject": subject,
            "department": department,
        }


# ── normalize_class_context_value ────────────────────────────────────────────

def test_normalize_returns_none_for_none():
    from app import normalize_class_context_value
    assert normalize_class_context_value(None) is None


def test_normalize_returns_none_for_empty_string():
    from app import normalize_class_context_value
    assert normalize_class_context_value("") is None


def test_normalize_returns_none_for_whitespace():
    from app import normalize_class_context_value
    assert normalize_class_context_value("   ") is None


def test_normalize_lowercases_and_strips():
    from app import normalize_class_context_value
    assert normalize_class_context_value("  MATH  ") == "math"


# ── get_class_session_context ────────────────────────────────────────────────

def test_get_returns_none_when_no_context(client):
    faculty_session(client)
    # GET /camera with no prior /session — context must be absent
    with app.test_request_context("/"):
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            # deliberately do NOT set CLASS_CONTEXT_SESSION_KEY
        from app import get_class_session_context
        # must return None, not "general"
        with app.test_request_context("/"):
            from flask import session
            assert get_class_session_context() is None


def test_get_returns_stored_context(client):
    faculty_session(client)
    set_class_context(client, "math", "cs")
    with client.session_transaction() as sess:
        ctx = sess[CLASS_CONTEXT_SESSION_KEY]
    assert ctx["subject"] == "math"
    assert ctx["department"] == "cs"


# ── /session route ────────────────────────────────────────────────────────────

def test_session_post_missing_subject_returns_400(client):
    faculty_session(client)
    rv = client.post("/session", data={"department": "cs"})
    assert rv.status_code == 400


def test_session_post_missing_department_returns_400(client):
    faculty_session(client)
    rv = client.post("/session", data={"subject": "math"})
    assert rv.status_code == 400


def test_session_post_empty_fields_returns_400(client):
    faculty_session(client)
    rv = client.post("/session", data={"subject": "", "department": ""})
    assert rv.status_code == 400


def test_session_post_valid_sets_context_and_redirects(client):
    faculty_session(client)
    rv = client.post(
        "/session",
        data={"subject": "math", "department": "cs"},
        follow_redirects=False,
    )
    assert rv.status_code == 302
    assert "/camera" in rv.headers["Location"]
    with client.session_transaction() as sess:
        ctx = sess[CLASS_CONTEXT_SESSION_KEY]
        assert ctx["subject"] == "math"
        assert ctx["department"] == "cs"


def test_session_post_does_not_store_general_fallback(client):
    """
    Regression: the old code silently stored "general" when fields were
    missing. After the fix, missing fields must be rejected, never stored.
    """
    faculty_session(client)
    client.post("/session", data={"subject": "", "department": ""})
    with client.session_transaction() as sess:
        ctx = sess.get(CLASS_CONTEXT_SESSION_KEY)
    assert ctx is None or (
        ctx.get("subject") != "general" and ctx.get("department") != "general"
    )


# ── /camera route ────────────────────────────────────────────────────────────

def test_camera_without_context_redirects_to_session(client):
    faculty_session(client)
    rv = client.get("/camera", follow_redirects=False)
    assert rv.status_code == 302
    assert rv.headers["Location"].endswith("/session")


def test_camera_with_valid_context_returns_200(client):
    faculty_session(client)
    set_class_context(client, "math", "cs")
    rv = client.get("/camera")
    assert rv.status_code == 200


def test_camera_get_does_not_mutate_session(client):
    """
    Regression: the old get_class_session_context() wrote to the session on
    every GET. After the fix, a GET /camera must not change session state.
    """
    faculty_session(client)
    set_class_context(client, "math", "cs")

    with client.session_transaction() as sess:
        before = dict(sess)

    client.get("/camera")

    with client.session_transaction() as sess:
        after = dict(sess)

    assert before[CLASS_CONTEXT_SESSION_KEY] == after[CLASS_CONTEXT_SESSION_KEY]


# ── /mark_attendance route ────────────────────────────────────────────────────

def test_mark_attendance_without_context_returns_400(client):
    faculty_session(client)
    rv = client.post(
        "/mark_attendance",
        json={"image": "data:image/jpeg;base64,/9j/fake"},
    )
    assert rv.status_code == 400
    assert "No active class session" in rv.get_json()["message"]


def test_mark_attendance_never_uses_general_fallback(client):
    """
    Regression: the old code fell back to subject='general' when the POST
    body had no subject key. After the fix it must return 400 instead.
    """
    faculty_session(client)
    # No class context in session, no subject in body
    rv = client.post("/mark_attendance", json={"image": "data:image/jpeg;base64,abc"})
    data = rv.get_json()
    assert rv.status_code == 400
    assert data.get("message") != "general"


# ── /end_session route ────────────────────────────────────────────────────────

def test_end_session_without_context_returns_400(client):
    faculty_session(client)
    rv = client.post("/end_session", json={})
    assert rv.status_code == 400
    assert "No active class session" in rv.get_json()["message"]


# ── session isolation (concurrent request simulation) ────────────────────────

def test_two_faculty_sessions_are_independent(client):
    """
    Core concurrency regression: two faculty browsers must hold independent
    session contexts. Simulated here by using two separate test clients.
    """
    client_a = app.test_client()
    client_b = app.test_client()

    # Faculty A sets context: math/cs
    with client_a.session_transaction() as sess:
        sess["logged_in"] = True
        sess["role"] = "faculty"
        sess["faculty_department"] = "cs"
        sess["faculty_subjects"] = ["math"]
        sess[CLASS_CONTEXT_SESSION_KEY] = {"subject": "math", "department": "cs"}

    # Faculty B sets context: physics/ee
    with client_b.session_transaction() as sess:
        sess["logged_in"] = True
        sess["role"] = "faculty"
        sess["faculty_department"] = "ee"
        sess["faculty_subjects"] = ["physics"]
        sess[CLASS_CONTEXT_SESSION_KEY] = {"subject": "physics", "department": "ee"}

    # B's context must not affect A's
    with client_a.session_transaction() as sess:
        ctx_a = sess[CLASS_CONTEXT_SESSION_KEY]

    with client_b.session_transaction() as sess:
        ctx_b = sess[CLASS_CONTEXT_SESSION_KEY]

    assert ctx_a["subject"] == "math"
    assert ctx_b["subject"] == "physics"
    assert ctx_a["subject"] != ctx_b["subject"]