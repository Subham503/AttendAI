from flask import Flask, render_template, request, redirect, Response, jsonify, session, make_response, send_file
import cv2
from datetime import datetime, timedelta
from html import unescape
from alerts import init_mail, send_low_attendance_alert
from supabase import create_client
import os
import io
import atexit
import re
import threading
import numpy as np
import bcrypt
import time as _time
import pickle
import uuid
import base64
from pathlib import Path
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from report_engine import generate_attendance_pdf
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from liveness import verify_liveness
import qrcode

# Reg-no must be purely alphanumeric with optional hyphens/underscores,
# 2-30 characters. Rejects any path traversal sequence (dots, slashes, etc.).
_REG_NO_RE = re.compile(r'^[A-Z0-9][A-Z0-9_-]{1,29}$')
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
# Load environment variables
load_dotenv()

app = Flask(__name__)
secret_key = os.getenv("SECRET_KEY")
if not secret_key:
    raise RuntimeError("SECRET_KEY is required. Set a secret key in .env before starting the app.")

app.secret_key = secret_key
init_mail(app)
# Session timeout configuration
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
    minutes=int(os.getenv("SESSION_TIMEOUT_MINUTES", 30))
)

# Rate limiter.
# In production, set RATELIMIT_STORAGE_URI to a Redis URL so counters
# survive process restarts and are shared across all workers:
#   RATELIMIT_STORAGE_URI=redis://:password@host:6379/0
# Without Redis, each process restart resets all counters, and each
# gunicorn worker tracks its own counters independently, effectively
# multiplying the configured limit by the number of workers.
_RATELIMIT_STORAGE = os.getenv(
    "RATELIMIT_STORAGE_URI",
    os.getenv("REDIS_URL", "memory://"),
)
if _RATELIMIT_STORAGE == "memory://":
    import warnings
    warnings.warn(
        "Rate limiter is using in-memory storage. Counters reset on restart "
        "and are not shared across workers. Set RATELIMIT_STORAGE_URI to a "
        "Redis URL in production.",
        RuntimeWarning,
        stacklevel=1,
    )

limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=_RATELIMIT_STORAGE,
    default_limits=["200 per day", "50 per hour"],
)


# ===== Request body size limit (DoS hardening, issue #50) =====
# Reject any request body larger than 5 MB at the Flask/Werkzeug layer
# BEFORE the JSON parser or base64 decoder ever touches it. This protects
# every POST endpoint (not just /mark_attendance) from memory-exhaustion
# attacks via oversized payloads.
app.config["MAX_CONTENT_LENGTH"] = int(
    os.getenv("MAX_CONTENT_LENGTH", 5 * 1024 * 1024)  # 5 MB default
)

# ===== Image payload size limits (issue #50) =====
# Hard cap on a single decoded image (bytes) and on the base64 data-URI
# string that carries it. base64 expands binary by ~33%, so the encoded
# string limit is 1.4x the decoded limit. Both checks run BEFORE
# cv2.imdecode() so an attacker cannot force OpenCV to allocate a huge
# framebuffer.
MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", 2 * 1024 * 1024))  # 2 MB decoded
MAX_IMAGE_DATA_URI_BYTES = int(
    os.getenv("MAX_IMAGE_DATA_URI_BYTES", int(MAX_IMAGE_BYTES * 1.4))  # ~2.8 MB encoded
)
# /register accepts an array of frames; cap both the per-frame size and the
# total frame count so a single request cannot flood the server.
MAX_REGISTER_FRAMES = int(os.getenv("MAX_REGISTER_FRAMES", 30))

# ===== Low Attendance Alert Configuration =====
LOW_ATTENDANCE_THRESHOLD = float(
    os.getenv("LOW_ATTENDANCE_THRESHOLD", 75)
)

ALERT_SCHEDULE_DAY = os.getenv(
    "ALERT_SCHEDULE_DAY", "mon"
)

ALERT_SCHEDULE_HOUR = int(
    os.getenv("ALERT_SCHEDULE_HOUR", 8)
)

# ===== Supabase =====
supabase_client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

CLASS_CONTEXT_SESSION_KEY = "class_session_context"

# ================= QR CHECK-IN TOKEN STORE =================
# In-memory store: { token_str: { subject, department, faculty_id, created_at, expires_at } }
# Same in-memory approach as the LBPH model, circuit breaker, etc.
_qr_sessions = {}
_qr_lock = threading.Lock()

QR_EXPIRY_MINUTES = int(os.getenv("QR_EXPIRY_MINUTES", 15))



def _cleanup_expired_qr_tokens():
    """Remove expired QR tokens. Called periodically by APScheduler."""
    now = datetime.now()
    with _qr_lock:
        expired = [t for t, data in _qr_sessions.items() if data["expires_at"] <= now]
        for t in expired:
            del _qr_sessions[t]
        if expired:
            print(f"🧹 Cleaned up {len(expired)} expired QR token(s)")


def normalize_class_context_value(value):
    value = (value or "general").strip().lower()
    return value or "general"


def sanitize_text_field(value):
    text = unescape(str(value or "")).strip()
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def decode_image_payload(image_data):
    """Decode a base64 data-URI image into an OpenCV frame with strict
    size limits.

    Security (issue #50): every size check runs BEFORE the heavy
    ``base64.b64decode`` / ``cv2.imdecode`` calls so an attacker cannot
    force the server to allocate a huge buffer. Returns ``(frame, error)``
    where exactly one of the two is non-None:

        * on success:  ``(frame, None)``
        * on failure:  ``(None, (status_code, message))``

    Args:
        image_data: a string like ``"data:image/jpeg;base64,/9j/4AAQ..."``
            or a raw base64 string. Anything falsy or non-string is rejected.

    Enforced limits (configurable via env vars):
        * ``MAX_IMAGE_DATA_URI_BYTES`` — max length of the encoded string.
        * ``MAX_IMAGE_BYTES`` — max size of the decoded binary blob.
    """
    if not isinstance(image_data, str) or not image_data:
        return None, (400, "No image provided.")

    # Layer 1: reject oversized *encoded* strings before decoding.
    if len(image_data) > MAX_IMAGE_DATA_URI_BYTES:
        return None, (
            413,
            f"Image too large. Maximum encoded size is "
            f"{MAX_IMAGE_DATA_URI_BYTES // 1024} KB.",
        )

    # Split optional ``data:...;base64,`` header. The header is mandatory
    # for browser data URIs but tolerate raw base64 for non-browser clients.
    encoded = image_data
    if "," in image_data:
        _header, encoded = image_data.split(",", 1)

    # Layer 2: cap the decoded length. ``base64.b64decode`` with
    # ``validate=True`` rejects non-alphabet characters so an attacker
    # cannot smuggle in extra bytes via padding tricks.
    try:
        img_bytes = base64.b64decode(encoded, validate=True)
    except Exception:
        return None, (400, "Invalid base64 image data.")

    if len(img_bytes) > MAX_IMAGE_BYTES:
        return None, (
            413,
            f"Decoded image exceeds maximum allowed size of "
            f"{MAX_IMAGE_BYTES // 1024} KB.",
        )

    # Layer 3: decode with OpenCV. ``imdecode`` is the real memory sink
    # — it allocates a full framebuffer — so we only reach it after the
    # byte-size checks above have passed.
    try:
        np_arr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    except Exception:
        return None, (400, "Failed to decode image.")

    if frame is None:
        return None, (400, "Invalid image received.")

    return frame, None


def set_class_session_context(subject, department):
    context = {
        "subject": normalize_class_context_value(subject),
        "department": normalize_class_context_value(department),
    }
    session[CLASS_CONTEXT_SESSION_KEY] = context
    session.modified = True
    return context


def get_class_session_context(data=None):
    data = data or {}
    stored = session.get(CLASS_CONTEXT_SESSION_KEY) or {}
    subject = data.get("subject") or stored.get("subject") or request.args.get("subject")
    department = data.get("department") or stored.get("department") or request.args.get("department")
    return set_class_session_context(subject, department)


def _normalize_scope_value(value):
    if value is None:
        return ""
    return str(value).strip().lower()


def _parse_scope_list(value):
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = re.split(r"[,;\n|]+", str(value))
    return sorted({
        normalized
        for item in raw_values
        if (normalized := _normalize_scope_value(item))
    })


def _extract_faculty_subjects(user):
    for key in ("subjects", "assigned_subjects", "subject"):
        subjects = _parse_scope_list(user.get(key))
        if subjects:
            return subjects
    return []


def _current_faculty_scope():
    if session.get("role") != "faculty":
        return None
    return {
        "department": _normalize_scope_value(session.get("faculty_department")),
        "subjects": _parse_scope_list(session.get("faculty_subjects")),
    }


def _scope_has_limits(scope):
    return bool(scope and (scope["department"] or scope["subjects"]))


def _record_matches_faculty_scope(record, scope):
    if not _scope_has_limits(scope):
        return False

    if scope["department"]:
        record_department = _normalize_scope_value(record.get("department"))
        if record_department != scope["department"]:
            return False

    if scope["subjects"]:
        record_subject = _normalize_scope_value(record.get("subject"))
        if record_subject not in scope["subjects"]:
            return False

    return True


def _current_user_can_access_class(department, subject):
    if session.get("role") != "faculty":
        return True

    scope = _current_faculty_scope()
    if not _scope_has_limits(scope):
        return False

    if scope["department"] and _normalize_scope_value(department) != scope["department"]:
        return False

    if scope["subjects"] and _normalize_scope_value(subject) not in scope["subjects"]:
        return False

    return True


def _attendance_query_for_current_user(select_columns="*"):
    query = supabase_client.table("attendance").select(select_columns)
    scope = _current_faculty_scope()

    if session.get("role") != "faculty":
        return query

    if not _scope_has_limits(scope):
        return None

    if scope["department"]:
        query = query.ilike("department", scope["department"])
    if scope["subjects"]:
        query = query.in_("subject", scope["subjects"])
    return query


def _fetch_scoped_attendance(select_columns="*"):
    query = _attendance_query_for_current_user(select_columns)
    if query is None:
        return []
    result = query.execute()
    return result.data or []

# Global model and label map for efficiency
global_recognizer = cv2.face.LBPHFaceRecognizer_create()
global_label_map = {}

# ================= MODEL LOADING =================
def load_model():
    global global_recognizer, global_label_map
    if os.path.exists("trainer.yml") and os.path.exists("labels.pickle"):
        try:
            global_recognizer.read("trainer.yml")
            with open("labels.pickle", "rb") as f:
                global_label_map = pickle.load(f)
            print("✅ Face Recognition Model loaded from disk")
            return True
        except Exception as e:
            print(f"❌ Error loading model: {e}")
    return False

# ================= TRAIN FACE MODEL =================
def train_model():
    global global_recognizer, global_label_map
    face_cascade = cv2.CascadeClassifier("haarcascade_frontalface_default.xml")

    result = supabase_client.table('students').select('id, name, reg_no, department, class').execute()
    students = [(s['id'], s['name'], s['reg_no'], s['department'], s['class']) for s in (result.data or [])]

    if not students:
        print("❌ No students in DB")
        return None, {}

    student_map = {s[2]: s for s in students}
    faces_data = []
    labels = []
    label_map = {}
    label_counter = 0

    image_folder = "images"
    if not os.path.exists(image_folder):
        print("❌ No images folder")
        return None, {}

    reg_no_files = {}
    for filename in sorted(os.listdir(image_folder)):
        if not filename.lower().endswith(('.jpg', '.jpeg', '.png')):
            continue
        base = os.path.splitext(filename)[0]
        reg_no = base.rsplit('_', 1)[0] if '_' in base else base
        if reg_no not in student_map:
            continue
        if reg_no not in reg_no_files:
            reg_no_files[reg_no] = []
        reg_no_files[reg_no].append(filename)

    for reg_no, files in sorted(reg_no_files.items()):
        student = student_map[reg_no]
        added = 0
        for filename in files:
            img_path = os.path.join(image_folder, filename)
            img = cv2.imread(img_path)
            if img is None:
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            detected = face_cascade.detectMultiScale(gray, 1.3, 5)
            if len(detected) == 0:
                continue
            x, y, w, h = detected[0]
            face_roi = cv2.resize(gray[y:y+h, x:x+w], (200, 200))
            faces_data.append(face_roi)
            labels.append(label_counter)
            added += 1

        if added > 0:
            label_map[label_counter] = student
            print(f"🏷️ Label {label_counter} → {reg_no} ({student[1]}) — {added} photo(s)")
            label_counter += 1

    if len(faces_data) == 0:
        print("❌ No valid face data")
        return None, {}

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(faces_data, np.array(labels))

    # Save to disk
    recognizer.save("trainer.yml")
    with open("labels.pickle", "wb") as f:
        pickle.dump(label_map, f)

    # Update globals
    global_recognizer = recognizer
    global_label_map = label_map

    print(f"✅ Trained and saved: {label_counter} student(s), {len(faces_data)} total faces")
    return recognizer, label_map

# Load model once at startup
load_model()

# ================= ASYNC RETRAINING =================
_retrain_lock   = threading.Lock()
_retrain_status = {'running': False, 'last_completed': 'never', 'student_count': 0}


def retrain_in_background():
    """
    Runs train_model() in a daemon thread so /register returns immediately.
    _retrain_lock prevents concurrent registrations from triggering overlapping
    retrains — if one is already running, the new request is silently skipped.
    _retrain_status is written only from this function (single writer) so
    CPython's GIL makes individual key assignments safe to read from Flask threads.
    """
    acquired = _retrain_lock.acquire(blocking=False)
    if not acquired:
        print("[Retrain] Skipped — retrain already in progress.")
        return
    try:
        _retrain_status['running'] = True
        try:
            _, label_map = train_model()
            _retrain_status['student_count'] = len(label_map) if label_map else 0
            _retrain_status['last_completed'] = datetime.now().strftime('%H:%M:%S')
            print(f"[Retrain] Complete — {_retrain_status['student_count']} student(s) loaded.")
        except Exception as e:
            print(f"[Retrain] Error: {e}")
        finally:
            _retrain_status['running'] = False
    finally:
        _retrain_lock.release()

# ─────────────────────────────────────────────────────────────────────────────

# ================= SESSION PROTECTION =================
@app.before_request
def require_login():

    public_routes = [
    '/login',
    '/register',
    '/static',
    '/checkin',
]

    # Allow public pages
    if any(request.path.startswith(route) for route in public_routes):
        return

    # Redirect if session expired or not logged in
    if not session.get('logged_in'):
        return redirect('/login')

# ================= HOME — protected =================
@app.route('/')
def index():
    if session.get('role') == 'student':
        return redirect('/student_dashboard')
    return render_template("index.html")

# ================= LOGIN PAGE =================
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    # Already logged in → go home
    if session.get('logged_in'):
        return redirect('/')

    if request.method == 'GET':
        return render_template('login.html')

    # POST — comes as JSON from the login form JS or form data
    data = request.get_json(force=True, silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    role     = data.get('role', 'admin')
    next_url = data.get('next', '').strip()

    # Validate next_url to prevent open redirect vulnerabilities
    if not next_url.startswith('/') or next_url.startswith('//'):
        next_url = '/'

    if role == 'admin':
        result = supabase_client.table('admins').select('password').eq('username', username).execute()
        user = result.data[0] if result.data else None
        if user and bcrypt.checkpw(password.encode(), user['password'].encode()):
            session.permanent = True
            session['logged_in'] = True
            session['role']      = 'admin'
            session['name']      = username
            return jsonify({'success': True, 'redirect': next_url})
        return jsonify({'success': False, 'message': 'Invalid admin credentials.'})

    elif role == 'faculty':
        result = supabase_client.table('faculty').select('*').eq('faculty_id', username).execute()
        user = result.data[0] if result.data else None
        if user and bcrypt.checkpw(password.encode(), user['password'].encode()):
            session.permanent = True
            session['logged_in'] = True
            session['role']      = 'faculty'
            session['name']      = user['name'] if user['name'] else username
            session['faculty_id'] = username
            session['faculty_department'] = _normalize_scope_value(user.get('department'))
            session['faculty_subjects'] = _extract_faculty_subjects(user)
            return jsonify({'success': True, 'redirect': next_url})
        return jsonify({'success': False, 'message': 'Invalid faculty credentials.'})

    elif role == 'student':
        result = supabase_client.table('students').select('password, name').eq('reg_no', username).execute()
        user = result.data[0] if result.data else None
        if user and bcrypt.checkpw(password.encode(), user['password'].encode()):
            session.permanent = True
            session['logged_in'] = True
            session['role']      = 'student'
            session['name']      = user['name']
            session['reg_no']    = username
            return jsonify({'success': True, 'redirect': next_url})
        return jsonify({'success': False, 'message': 'Invalid student credentials.'})

    return jsonify({'success': False, 'message': 'Invalid credentials.'})


@app.errorhandler(429)
def too_many_requests(error):
    if request.path == '/login' and request.method == 'POST':
        return jsonify({'success': False, 'message': 'Too many login attempts. Please wait a minute and try again.'}), 429
    return jsonify({'success': False, 'message': 'Too many requests.'}), 429

@app.errorhandler(413)
def request_entity_too_large(error):
    """Issue #50: return a clean JSON 413 when MAX_CONTENT_LENGTH is exceeded
    so API clients (camera JS, register form, etc.) get a parseable error
    instead of Werkzeug's default HTML response."""
    limit_kb = app.config.get("MAX_CONTENT_LENGTH", 0) // 1024
    return jsonify({
        'success': False,
        'message': f'Request body too large. Maximum size is {limit_kb} KB.'
    }), 413

# ================= LOGOUT =================
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ================= RETRAIN MODEL (Admin Only) =================
@app.route('/retrain')
def retrain():
    if not session.get('logged_in') or session.get('role') != 'admin':
        return "❌ Access Denied: Admin only route."

    recognizer, label_map = train_model()
    if recognizer:
        return "✅ Model retrained and saved successfully! <a href='/'>Go Home</a>"
    return "❌ Training failed. Make sure students have registered photos."

# ================= STUDENT DASHBOARD =================
@app.route('/student_dashboard')
def student_dashboard():
    if not session.get('logged_in') or session.get('role') != 'student':
        return redirect('/login')
    reg_no = session.get('reg_no')
 
    student_res = supabase_client.table('students').select('id').eq('reg_no', reg_no).execute()
    student_id = student_res.data[0]['id'] if student_res.data else None
 
    records = []
    subject_stats = []
 
    if student_id:
        records_res = (
            supabase_client.table('attendance')
            .select('subject, date, time, status')
            .eq('student_id', student_id)
            .order('date', desc=True)
            .order('time', desc=True)
            .execute()
        )
        records = [(r['subject'], r['date'], r['time'], r['status']) for r in (records_res.data or [])]
 
        # Build per-subject present/total counts from status field
        subject_counts = {}
        for subject, _date, _time, status in records:
            counts = subject_counts.setdefault(subject, {'present': 0, 'total': 0})
            counts['total'] += 1
            if status == 'Present':
                counts['present'] += 1
 
        for subject, counts in subject_counts.items():
            present = counts['present']
            total = counts['total']
            pct = round((present / total) * 100) if total else 0
            subject_stats.append((subject, present, total, pct))
 
        subject_stats.sort(key=lambda x: x[0])
 
    return render_template(
        "student_dashboard.html",
        name=session.get('name'),
        reg_no=reg_no,
        records=records,
        subject_stats=subject_stats,
    )

# ================= CLASS SESSION =================
@app.route('/session', methods=['GET', 'POST'])
def class_session():
    if not session.get('logged_in'):
        return redirect('/login')
    if session.get('role') not in ['admin', 'faculty']:
        return render_template('403.html'), 403
    if request.method == 'POST':
        subject    = request.form.get('subject', 'general').strip().lower()
        department = request.form.get('department', 'general').strip().lower()
        if not _current_user_can_access_class(department, subject):
            return render_template('403.html'), 403
        set_class_session_context(subject, department)
        return redirect(f'/camera?subject={subject}&department={department}')
    return render_template("class_session.html")

# ================= REGISTER =================
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        return render_template("register.html")

    data = request.get_json(force=True, silent=True) or {}
    name       = sanitize_text_field(data.get('name', ''))
    reg_no     = data.get('reg_no', '').strip().upper()
    department = sanitize_text_field(data.get('department', '')).upper()
    class_name = sanitize_text_field(data.get('class_name', ''))
    password   = data.get('password', '').strip()
    email      = sanitize_text_field(data.get('email', '')).lower()
    frames     = data.get('frames', [])

    if not all([name, reg_no, department, class_name, password]):
        return jsonify({'success': False, 'message': 'Missing required fields.'})

    if email and not _EMAIL_RE.match(email):
        return jsonify({'success': False, 'message': 'Invalid email format.'})

    # Layer 1: validate reg_no format — only uppercase letters, digits,
    # hyphens, and underscores are allowed (2-30 chars). This rejects any
    # path traversal sequence such as "../", "//", or null bytes before the
    # value ever reaches the filesystem.
    if not _REG_NO_RE.match(reg_no):
        return jsonify({
            'success': False,
            'message': 'Invalid registration number. Use only letters, digits, hyphens, and underscores (2-30 characters).'
        })

    # Issue #50: cap the number of frames per request so a malicious client
    # cannot flood the server with thousands of oversized images in a single
    # POST. Combined with the global MAX_CONTENT_LENGTH this bounds the total
    # memory pressure of any single /register call.
    if not isinstance(frames, list) or len(frames) > MAX_REGISTER_FRAMES:
        return jsonify({
            'success': False,
            'message': f'Too many frames. Maximum is {MAX_REGISTER_FRAMES}.'
        })

    if len(frames) < 20:
        return jsonify({'success': False, 'message': f'Need 20 frames, got {len(frames)}.'})

    hashed_pwd = bcrypt.hashpw(password.encode(), bcrypt.gensalt())

    images_dir = Path("images").resolve()
    images_dir.mkdir(parents=True, exist_ok=True)  # replaces the old os.makedirs call

    face_cascade = cv2.CascadeClassifier("haarcascade_frontalface_default.xml")
    saved = 0

    for i, frame_data in enumerate(frames):
        # Issue #50: per-frame size validation via the shared helper. A
        # single oversized frame is skipped rather than aborting the whole
        # registration, so a flaky client doesn't waste the user's earlier
        # good frames — but the oversized frame is never decoded.
        frame, img_err = decode_image_payload(frame_data)
        if img_err is not None:
            print(f"Frame {i} rejected: {img_err[1]}")
            continue
        try:
            gray     = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            detected = face_cascade.detectMultiScale(gray, 1.3, 5)
            if len(detected) == 0:
                continue

            # Layer 2: path confinement — resolve the absolute path and
            # confirm it stays inside images_dir before writing anything.
            photo_path = (images_dir / f"{reg_no}_{saved + 1}.jpg").resolve()
            if not photo_path.is_relative_to(images_dir):
                print(f"Path traversal attempt blocked for reg_no={reg_no!r}")
                continue

            cv2.imwrite(str(photo_path), frame)
            saved += 1
        except Exception as e:
            print(f"Frame {i} error: {e}")

    if saved == 0:
        return jsonify({'success': False, 'message': 'No face detected in any frame. Retry in better lighting.'})

    if not all([name, reg_no, department, class_name, password]):
        return jsonify({'success': False, 'message': 'Missing required fields.'})

    if email and not _EMAIL_RE.match(email):
        return jsonify({'success': False, 'message': 'Invalid email format.'})

    # Layer 1: validate reg_no format — only uppercase letters, digits,
    # hyphens, and underscores are allowed (2-30 chars). This rejects any
    # path traversal sequence such as "../", "//", or null bytes before the
    # value ever reaches the filesystem.
    if not _REG_NO_RE.match(reg_no):
        return jsonify({
            'success': False,
            'message': 'Invalid registration number. Use only letters, digits, hyphens, and underscores (2-30 characters).'
        })

    if len(frames) < 20:
        return jsonify({'success': False, 'message': f'Need 20 frames, got {len(frames)}.'})

    hashed_pwd = bcrypt.hashpw(password.encode(), bcrypt.gensalt())

    images_dir = Path("images").resolve()
    images_dir.mkdir(parents=True, exist_ok=True)  # replaces the old os.makedirs call

    face_cascade = cv2.CascadeClassifier("haarcascade_frontalface_default.xml")
    saved = 0

    for i, frame_data in enumerate(frames):
        try:
            header, encoded = frame_data.split(',', 1)
            img_bytes = base64.b64decode(encoded)
            np_arr    = np.frombuffer(img_bytes, np.uint8)
            frame     = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if frame is None:
                continue
            gray     = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            detected = face_cascade.detectMultiScale(gray, 1.3, 5)
            if len(detected) == 0:
                continue

            # Layer 2: path confinement — resolve the absolute path and
            # confirm it stays inside images_dir before writing anything.
            photo_path = (images_dir / f"{reg_no}_{saved + 1}.jpg").resolve()
            if not photo_path.is_relative_to(images_dir):
                print(f"Path traversal attempt blocked for reg_no={reg_no!r}")
                continue

            cv2.imwrite(str(photo_path), frame)
            saved += 1
        except Exception as e:
            print(f"Frame {i} error: {e}")

    if saved == 0:
        return jsonify({'success': False, 'message': 'No face detected in any frame. Retry in better lighting.'})

    try:
        supabase_client.table('students').insert({
            'name': name,
            'reg_no': reg_no,
            'department': department,
            'class': class_name,
            'password': hashed_pwd.decode('utf-8'),
            'email': email or None
        }).execute()
    except Exception as e:
        return jsonify({'success': False, 'message': f'DB error: {str(e)}'})

    # Kick off background retrain — response returns immediately
    threading.Thread(target=retrain_in_background, daemon=True).start()

    return jsonify({'success': True, 'message': f'Enrolled {name} with {saved} face photos.'})

# ================= CAMERA PAGE =================
@app.route('/camera')
def camera():
    if not session.get('logged_in'):
        return redirect('/login')
    if session.get('role') not in ['admin', 'faculty']:
        return render_template('403.html'), 403
    context = get_class_session_context()
    if not _current_user_can_access_class(context["department"], context["subject"]):
        return render_template('403.html'), 403
    return render_template("camera.html",
                           subject=context["subject"],
                           department=context["department"])

class CircuitBreaker:
    """
    Thread-safe circuit breaker.
    States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (probing) → CLOSED

    ⚠️ KNOWN LIMITATION: supabase_with_retry() uses time.sleep() for backoff
    delays (0.5s → 1s → 2s). Since Flask is synchronous, this blocks the
    entire thread during retries — meaning other requests queue up for up to
    3.5s per failed Supabase call. Acceptable for this use case (low-traffic
    classroom tool), but should be replaced with async/gevent if concurrency
    becomes a concern.
    """
    CLOSED    = 'CLOSED'
    OPEN      = 'OPEN'
    HALF_OPEN = 'HALF_OPEN'

    def __init__(self, failure_threshold=3, recovery_timeout=30):
        self.failure_threshold = failure_threshold
        self.recovery_timeout  = recovery_timeout
        self._state            = self.CLOSED
        self._failure_count    = 0
        self._opened_at        = None
        self._lock             = threading.Lock()   # 🔒 guards all state transitions

    @property
    def state(self):
        with self._lock:
            if self._state == self.OPEN:
                if _time.time() - self._opened_at >= self.recovery_timeout:
                    self._state = self.HALF_OPEN
            return self._state

    def record_success(self):
        with self._lock:
            self._failure_count = 0
            self._state         = self.CLOSED

    def record_failure(self):
        with self._lock:
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._state     = self.OPEN
                self._opened_at = _time.time()

    def is_open(self):
        return self.state == self.OPEN


# Module-level singleton shared across all requests
_supabase_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)


def supabase_with_retry(operation_fn):
    """
    Wraps a Supabase call with circuit breaker + exponential backoff retry.
    3 attempts with delays: 0.5s → 1s → 2s.

    ⚠️ NOTE: time.sleep() here blocks the Flask thread for up to 3.5s on
    full retry exhaustion. See CircuitBreaker docstring for details.
    """
    if _supabase_breaker.is_open():
        raise RuntimeError("DB_OPEN")

    delays   = [0.5, 1.0, 2.0]
    last_err = None

    for attempt, delay in enumerate(delays, start=1):
        try:
            result = operation_fn()
            _supabase_breaker.record_success()
            return result
        except Exception as e:
            last_err = e
            print(f"[Supabase] Attempt {attempt}/3 failed: {e}")
            _supabase_breaker.record_failure()
            if attempt < len(delays):
                _time.sleep(delay)   # ⚠️ blocks thread — see docstring above

    raise last_err
# ─────────────────────────────────────────────────────────────────────────────

# ================= MARK ATTENDANCE =================
@app.route('/mark_attendance', methods=['POST'])
def mark_attendance():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not logged in'}), 401
    if session.get('role') not in ['admin', 'faculty']:
        return jsonify({'success': False, 'message': 'Access denied: faculty or admin only'}), 403

    data       = request.get_json(silent=True) or {}
    image_data = data.get('image', '')
    context = get_class_session_context(data)
    subject = context["subject"]
    department = context["department"]
    if not _current_user_can_access_class(department, subject):
        return jsonify({'success': False, 'message': 'Access denied for this department or subject'}), 403

    if not global_label_map:
        return jsonify({'success': False, 'message': 'Model not trained. Admin must run /retrain first.'})

    # Issue #50: enforce size limits BEFORE base64 decode / cv2.imdecode
    # to prevent memory-exhaustion DoS via oversized image payloads.
    frame, img_err = decode_image_payload(image_data)
    if img_err is not None:
        status_code, message = img_err
        return jsonify({'success': False, 'message': message}), status_code

    # --- Server-Side Liveness Verification ---
    if not verify_liveness(frame):
        return jsonify({'success': False, 'message': 'Liveness check failed. Spoofing detected or no clear face.'})

    face_cascade = cv2.CascadeClassifier("haarcascade_frontalface_default.xml")
    gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    if len(faces) == 0:
        return jsonify({'success': False, 'message': 'No face detected. Position face clearly.'})

    CONFIDENCE_THRESHOLD = 60
    marked_names  = []
    skipped_names = []

    for (x, y, w, h) in faces:
        face_roi = cv2.resize(gray[y:y+h, x:x+w], (200, 200))
        label, confidence = global_recognizer.predict(face_roi)
        if confidence > CONFIDENCE_THRESHOLD:
            continue
        if label not in global_label_map:
            continue
        student = global_label_map[label]
        student_id, name, reg_no, dept, cls = student
        now = datetime.now()

        try:
            # ── Layer 1+2: retry + circuit breaker on SELECT ──────
            try:
                existing = supabase_with_retry(
                    lambda sid=student_id: supabase_client
                        .table('attendance')
                        .select('id')
                        .eq('student_id', sid)
                        .ilike('subject', subject)
                        .eq('date', str(now.date()))
                        .execute()
                )
            except RuntimeError as e:
                if 'DB_OPEN' not in str(e):
                    raise
                return jsonify({
                    'success': False,
                    'message': 'DB_OFFLINE',
                    'db_state': 'OPEN'
                })

            if existing.data:
                skipped_names.append(name)
            else:
                # ── Layer 1+2: retry + circuit breaker on INSERT ──
                try:
                    supabase_with_retry(
                        lambda: supabase_client.table('attendance').insert({
                            'student_id': student_id,
                            'name':       name,
                            'department': dept,
                            'class':      cls,
                            'subject':    subject,
                            'date':       str(now.date()),
                            'time':       str(now.time()),
                            'status':     'Present'
                        }).execute()
                    )
                    marked_names.append(name)
                except RuntimeError as e:
                    if 'DB_OPEN' in str(e):
                        return jsonify({
                            'success': False,
                            'message': 'DB_OFFLINE',
                            'db_state': 'OPEN'
                        })
                    raise

        except Exception as e:
            print(f"DB ERROR: {e}")
            return jsonify({
                'success': False,
                'message': 'DB_ERROR',
                'db_state': _supabase_breaker.state
            })

    if marked_names:
        return jsonify({'success': True, 'message': f'✅ Marked: {", ".join(marked_names)}'})
    elif skipped_names:
        return jsonify({'success': False, 'message': f'⚠️ Already marked today: {", ".join(skipped_names)}'})
    else:
        return jsonify({'success': False, 'message': 'Face detected but not recognized. Re-register in better lighting.'})

# ================= END SESSION =================
@app.route('/end_session', methods=['POST'])
def end_session():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not logged in'}), 401
    if session.get('role') not in ['admin', 'faculty']:
        return jsonify({'success': False, 'message': 'Access denied: faculty or admin only'}), 403

    data = request.get_json(silent=True) or {}
    context = get_class_session_context(data)
    subject = context["subject"]
    department = context["department"]
    if not _current_user_can_access_class(department, subject):
        return jsonify({'success': False, 'message': 'Access denied for this department or subject'}), 403

    now = datetime.now()
    date_str = str(now.date())
    time_str = str(now.time())

    try:
        students_res = supabase_client.table('students').select('id, name, department, class').eq('department', department).execute()
        all_students = students_res.data or []

        attendance_res = supabase_client.table('attendance').select('student_id').eq('subject', subject).eq('date', date_str).execute()
        present_student_ids = {record['student_id'] for record in (attendance_res.data or [])}

        absent_records = []
        for student in all_students:
            if student['id'] not in present_student_ids:
                absent_records.append({
                    'student_id': student['id'],
                    'name': student['name'],
                    'department': student['department'],
                    'class': student['class'],
                    'subject': subject,
                    'date': date_str,
                    'time': time_str,
                    'status': 'Absent'
                })

        if absent_records:
            supabase_client.table('attendance').insert(absent_records).execute()

        session.pop(CLASS_CONTEXT_SESSION_KEY, None)
        return jsonify({'success': True, 'message': f'Marked {len(absent_records)} absent.'})
    except Exception as e:
        print(f"DB ERROR in end_session: {e}")
        return jsonify({'success': False, 'message': 'Error marking absentees.'}), 500

# ================= ATTENDANCE =================
@app.route('/attendance')
def attendance():
    if not session.get('logged_in'):
        return redirect('/login')
    if session.get('role') not in ['admin', 'faculty']:
        return render_template('403.html'), 403

    data = []
    for r in _fetch_scoped_attendance('*'):
        data.append((
            r.get('id'), r.get('student_id'), r.get('name'), r.get('department'),
            r.get('class'), r.get('subject'), r.get('date'), r.get('time'), r.get('status')
        ))

    return render_template("attendance.html", data=data)

def scheduled_attendance_alert_job():
    """Automatically send low-attendance alerts."""
    print("Running scheduled attendance alerts...")

    try:
        students = supabase_client.table("students").select("*").execute()

        total_alerts = 0

        for student in (students.data or []):
            if student.get("email"):
                alerts = send_low_attendance_alert(
                    app,
                    supabase_client,
                    student,
                    LOW_ATTENDANCE_THRESHOLD
                )
                total_alerts += len(alerts)

        print(f"Attendance alert job complete. {total_alerts} alerts sent.")

    except Exception as e:
        print(f"Attendance alert job failed: {e}")

# ================= SCHEDULER SETUP =================
scheduler = BackgroundScheduler()

def weekly_report_job():
    print("Running weekly PDF report generation...")
    try:
        res = supabase_client.table('attendance').select('subject').execute()
        subjects = set(r['subject'] for r in (res.data or []) if r.get('subject'))

        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=7)

        for subj in subjects:
            pdf_bytes = generate_attendance_pdf(
                supabase_client,
                subject=subj,
                start_date=str(start_date),
                end_date=str(end_date)
            )
            # TODO: Upload pdf_bytes to Supabase Storage or email to faculty
            # to avoid local disk bloat as requested in PR review.
            pass
        print("Weekly reports generated (stateless mode).")
    except Exception as e:
        print(f"Error in weekly report job: {e}")

# Run every Friday at 17:00 (5 PM)
scheduler.add_job(
    func=weekly_report_job, trigger="cron", day_of_week='fri', hour=17, minute=0)

scheduler.add_job(
    func=scheduled_attendance_alert_job,
     trigger="cron",
    day_of_week=ALERT_SCHEDULE_DAY,
    hour=ALERT_SCHEDULE_HOUR,
    minute=0,
    id="attendance_alerts"
)

scheduler.add_job(
    func=_cleanup_expired_qr_tokens, trigger="interval", minutes=5, id="qr_cleanup")

scheduler.start()
atexit.register(lambda: scheduler.shutdown(wait=False))

# ================= EXPORT PDF =================
@app.route('/export_pdf')
def export_pdf():
    if not session.get('logged_in'):
        return redirect('/login')

    # Auth check: Only admin and faculty can export PDFs
    if session.get('role') not in ['admin', 'faculty']:
        return "Access Denied: Faculty/Admin only", 403

    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()
    subject = request.args.get('subject', '').strip().lower()

    if not start_date: start_date = None
    if not end_date: end_date = None
    if not subject: subject = None

    scope = _current_faculty_scope()
    department = None
    subjects = None
    if session.get('role') == 'faculty':
        if not _scope_has_limits(scope):
            return render_template('403.html'), 403
        department = scope["department"] or None
        subjects = scope["subjects"] or None
        if subject and not _current_user_can_access_class(department, subject):
            return render_template('403.html'), 403

    try:
        pdf_bytes = generate_attendance_pdf(
            supabase_client,
            subject,
            start_date,
            end_date,
            department=department,
            subjects=subjects,
        )
        buffer = io.BytesIO(pdf_bytes)
        buffer.seek(0)
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"attendance_report_{subject or 'all'}.pdf"
        )
    except Exception as e:
        return f"Error generating PDF: {str(e)}"

# ================= EXPORT CSV =================
@app.route('/export_csv')
def export_csv():
    if not session.get('logged_in'):
        return redirect('/login')
    if session.get('role') not in ['admin', 'faculty']:
        return render_template('403.html'), 403
    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID','Student ID','Name','Department','Class','Subject','Date','Time','Status'])
    for r in _fetch_scoped_attendance('*'):
        writer.writerow([r.get('id'),r.get('student_id'),r.get('name'),r.get('department'),r.get('class'),r.get('subject'),r.get('date'),r.get('time'),r.get('status')])
    output.seek(0)
    from flask import make_response
    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=attendance_report.csv'
    response.headers['Content-Type'] = 'text/csv'
    return response

# ================= DASHBOARD =================
@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'):
        return redirect('/login')
    if session.get('role') not in ['admin', 'faculty']:
        return render_template('403.html'), 403

    dept_counts = {}
    subj_counts = {}
    for r in _fetch_scoped_attendance('department, subject'):
        dept = r.get('department')
        subj = r.get('subject')
        dept_counts[dept] = dept_counts.get(dept, 0) + 1
        subj_counts[subj] = subj_counts.get(subj, 0) + 1

    dept_data = [(k, v) for k, v in dept_counts.items()]
    subject_data = [(k, v) for k, v in subj_counts.items()]

    return render_template("dashboard.html",
                           dept_data=dept_data,
                           subject_data=subject_data)

# ================= ALERTS =================
@app.route('/check-attendance-alerts', methods=['POST'])
def check_attendance_alerts():
    """Endpoint to trigger attendance alert checks"""

    if not session.get('logged_in') or session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    threshold = float(
        request.json.get('threshold', LOW_ATTENDANCE_THRESHOLD))

    students = supabase_client.table('students').select('*').execute()

    total_alerts = 0

    for student in students.data:
        if student.get('email'):
            alerts = send_low_attendance_alert(
                app,
                supabase_client,
                student,
                threshold
            )
            total_alerts += len(alerts)

    return jsonify({
        'message': f'Alert check complete. {total_alerts} alerts sent.',
        'alerts_sent': total_alerts
    })

# ================= DELETE =================
@app.route('/delete/<int:id>')
def delete(id):
    if not session.get('logged_in'):
        return redirect('/login')
    if session.get('role') not in ['admin', 'faculty']:
        return render_template('403.html'), 403

    existing = supabase_client.table('attendance').select('*').eq('id', id).execute()
    record = existing.data[0] if existing.data else None
    if not record:
        return redirect('/attendance')

    scope = _current_faculty_scope()
    if session.get('role') == 'faculty' and not _record_matches_faculty_scope(record, scope):
        return render_template('403.html'), 403

    supabase_client.table('attendance').delete().eq('id', id).execute()
    return redirect('/attendance')


@app.route('/retrain_status')
def retrain_status():
    """Returns background retrain state. Requires login."""
    if not session.get('logged_in'):
        return jsonify({'running': False, 'last_completed': 'unknown', 'student_count': 0}), 401
    return jsonify({
        'running': bool(_retrain_status.get('running')),
        'last_completed': _retrain_status.get('last_completed', 'unknown'),
        'student_count': int(_retrain_status.get('student_count') or 0),
    })


@app.route('/db_status')
def db_status():
    """Returns current circuit breaker state. Requires login."""
    if not session.get('logged_in'):
        return jsonify({'state': 'UNKNOWN'}), 401
    return jsonify({'state': _supabase_breaker.state})

# ================= QR CODE GENERATION (Faculty/Admin) =================
@app.route('/generate_qr', methods=['GET', 'POST'])
def generate_qr():
    if not session.get('logged_in'):
        return redirect('/login')
    if session.get('role') not in ['admin', 'faculty']:
        return render_template('403.html'), 403

    if request.method == 'GET':
        return render_template('generate_qr.html', expiry_minutes=QR_EXPIRY_MINUTES)

    # POST — generate a new QR token
    data = request.get_json(silent=True) or {}
    context = get_class_session_context(data)
    subject = context["subject"]
    department = context["department"]

    if not _current_user_can_access_class(department, subject):
        return jsonify({'success': False, 'message': 'Access denied for this department or subject'}), 403

    token = str(uuid.uuid4())
    now = datetime.now()
    expires_at = now + timedelta(minutes=QR_EXPIRY_MINUTES)

    with _qr_lock:
        _qr_sessions[token] = {
            'subject': subject,
            'department': department,
            'faculty_id': session.get('faculty_id', session.get('name', 'admin')),
            'created_at': now,
            'expires_at': expires_at,
        }

   
    # Build check-in URL. Prefer PUBLIC_BASE_URL in production; otherwise fall back to request.host_url (dev only).
    base_url = os.getenv("PUBLIC_BASE_URL", request.host_url)
    if not base_url.endswith('/'):
        base_url += '/'
    checkin_url = f"{base_url}checkin/{token}"

    # Generate QR image → base64 data URI
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2)
    qr.add_data(checkin_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#00fff7", back_color="#020810")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    qr_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')

    return jsonify({
        'success': True,
        'qr_image': f'data:image/png;base64,{qr_b64}',
        'token': token,
        'checkin_url': checkin_url,
        'subject': subject,
        'department': department,
        'expires_at': expires_at.isoformat(),
        'expiry_minutes': QR_EXPIRY_MINUTES,
    })

# ================= QR CHECK-IN (Student) =================
@app.route('/checkin/<token>')
def checkin(token):
    # If not logged in, redirect to login with return URL
    if not session.get('logged_in'):
        return redirect(f'/login?next=/checkin/{token}')

    if session.get('role') != 'student':
        return render_template('checkin.html',
                               error='Only students can check in via QR code.',
                               auto_checkin=False)

    # Validate token
    with _qr_lock:
        qr_data = _qr_sessions.get(token)

    if not qr_data:
        return render_template('checkin.html',
                               error='Invalid or expired QR code. Please ask your faculty for a new one.',
                               auto_checkin=False)

    if datetime.now() > qr_data['expires_at']:
        with _qr_lock:
            _qr_sessions.pop(token, None)
        return render_template('checkin.html',
                               error='This QR code has expired. Please ask your faculty for a new one.',
                               auto_checkin=False)

    # Look up student
    reg_no = session.get('reg_no')
    try:
         student_res = supabase_with_retry(
             lambda: supabase_client.table('students')
                 .select('id, name, department, class')
                 .eq('reg_no', reg_no)
                 .execute()
         )
    except RuntimeError as e:
         if 'DB_OPEN' in str(e):
             return render_template('checkin.html',
                                    error='Database temporarily unavailable. Please try again.',
                                    auto_checkin=False)
         raise
    student = student_res.data[0] if student_res.data else None

    if not student:
        return render_template('checkin.html',
                               error='Student record not found. Please contact admin.',
                               auto_checkin=False)

    subject = qr_data['subject']
    token_department = qr_data.get('department')
    if _normalize_scope_value(student.get('department')) != _normalize_scope_value(token_department):
         return render_template('checkin.html',
                                error='This QR code is for a different department. Please ask your faculty for the correct QR.',
                                auto_checkin=False)

    now = datetime.now()

    # Duplicate check
    try:
        existing = supabase_with_retry(
            lambda: supabase_client.table('attendance')
                .select('id')
                .eq('student_id', student['id'])
                .ilike('subject', subject)
                .ilike('department', token_department)
                .eq('date', str(now.date()))
                .execute()
                )
    except RuntimeError:
        return render_template('checkin.html',
                               error='Database temporarily unavailable. Please try again.',
                               auto_checkin=False)
    
    if existing.data:          # ← THIS MUST STAY
        return render_template('checkin.html',
                               error=None,
                               auto_checkin=True,
                               already_marked=True,
                               student_name=student['name'],
                               subject=subject,
                               department=qr_data['department'])
    
    # Mark attendance
    try:
        supabase_client.table('attendance').insert({
            'student_id': student['id'],
            'name': student['name'],
            'department': student['department'],
            'class': student['class'],
            'subject': subject,
            'date': str(now.date()),
            'time': str(now.time()),
            'status': 'Present'
        }).execute()
    except Exception as e:
        print(f"QR check-in DB error: {e}")
        return render_template('checkin.html',
                               error='Database error. Please try again.',
                               auto_checkin=False)

    return render_template('checkin.html',
                           error=None,
                           auto_checkin=True,
                           already_marked=False,
                           student_name=student['name'],
                           subject=subject,
                           department=qr_data['department'])

# ================= RUN =================
if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
