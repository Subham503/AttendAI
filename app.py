from flask import Flask, render_template, request, redirect, Response, jsonify, session
import cv2
from datetime import datetime
import mysql.connector
import os
import numpy as np
import bcrypt
import pickle
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "smartattend_secret_2024")

current_subject = "general"
current_department = "general"

# Global model and label map for efficiency
global_recognizer = cv2.face.LBPHFaceRecognizer_create()
global_label_map = {}

# ================= DATABASE =================
def connect_db():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "smart_attendance")
    )

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

    db = connect_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, name, reg_no, department, class FROM students")
    students = cursor.fetchall()
    db.close()

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

# ================= HOME — protected =================
@app.route('/')
def index():
    if not session.get('logged_in'):
        return redirect('/login')   # ✅ redirect to login if not logged in
    return render_template("index.html")

# ================= LOGIN PAGE =================
@app.route('/login', methods=['GET', 'POST'])
def login():
    # Already logged in → go home
    if session.get('logged_in'):
        return redirect('/')

    if request.method == 'GET':
        return render_template('login.html')

    # POST — comes as JSON from the login form JS
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    role     = data.get('role', 'admin')

    db = connect_db()
    cursor = db.cursor()

    if role == 'admin':
        cursor.execute("SELECT password FROM admins WHERE username=%s", (username,))
        user = cursor.fetchone()
        db.close()
        if user and bcrypt.checkpw(password.encode(), user[0].encode()):
            session['logged_in'] = True
            session['role']      = 'admin'
            session['name']      = username
            return jsonify({'success': True, 'redirect': '/'})
        return jsonify({'success': False, 'message': 'Invalid admin credentials.'})

    elif role == 'faculty':
        cursor.execute("SELECT password, name FROM faculty WHERE faculty_id=%s", (username,))
        user = cursor.fetchone()
        db.close()
        if user and bcrypt.checkpw(password.encode(), user[0].encode()):
            session['logged_in'] = True
            session['role']      = 'faculty'
            session['name']      = user[1] if user[1] else username
            return jsonify({'success': True, 'redirect': '/'})
        return jsonify({'success': False, 'message': 'Invalid faculty credentials.'})

    elif role == 'student':
        cursor.execute("SELECT password, name FROM students WHERE reg_no=%s", (username,))
        user = cursor.fetchone()
        db.close()
        if user and bcrypt.checkpw(password.encode(), user[0].encode()):
            session['logged_in'] = True
            session['role']      = 'student'
            session['name']      = user[1]
            session['reg_no']    = username
            return jsonify({'success': True, 'redirect': '/student_dashboard'})
        return jsonify({'success': False, 'message': 'Invalid student credentials.'})

    db.close()
    return jsonify({'success': False, 'message': 'Invalid credentials.'})

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
    db = connect_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT subject, date, time, status FROM attendance
        WHERE student_id = (SELECT id FROM students WHERE reg_no = %s)
        ORDER BY date DESC, time DESC
    """, (reg_no,))
    records = cursor.fetchall()
    cursor.execute("""
        SELECT subject, COUNT(*) FROM attendance
        WHERE student_id = (SELECT id FROM students WHERE reg_no = %s)
        GROUP BY subject
    """, (reg_no,))
    subject_stats = cursor.fetchall()
    db.close()
    return render_template("student_dashboard.html",
                           name=session.get('name'),
                           reg_no=reg_no,
                           records=records,
                           subject_stats=subject_stats)

# ================= CLASS SESSION =================
@app.route('/session', methods=['GET', 'POST'])
def class_session():
    if not session.get('logged_in'):
        return redirect('/login')
    if request.method == 'POST':
        subject    = request.form.get('subject', 'general').strip().lower()
        department = request.form.get('department', 'general').strip().lower()
        return redirect(f'/camera?subject={subject}&department={department}')
    return render_template("class_session.html")

# ================= REGISTER =================
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        return render_template("register.html")

    import base64
    data       = request.get_json()
    name       = data.get('name', '').strip()
    reg_no     = data.get('reg_no', '').strip().upper()
    department = data.get('department', '').strip().upper()
    class_name = data.get('class_name', '').strip()
    password   = data.get('password', '').strip()
    frames     = data.get('frames', [])

    if not all([name, reg_no, department, class_name, password]):
        return jsonify({'success': False, 'message': 'Missing required fields.'})

    if len(frames) < 20:
        return jsonify({'success': False, 'message': f'Need 20 frames, got {len(frames)}.'})

    hashed_pwd = bcrypt.hashpw(password.encode(), bcrypt.gensalt())

    if not os.path.exists("images"):
        os.makedirs("images")

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
            photo_path = f"images/{reg_no}_{saved + 1}.jpg"
            cv2.imwrite(photo_path, frame)
            saved += 1
        except Exception as e:
            print(f"Frame {i} error: {e}")

    if saved == 0:
        return jsonify({'success': False, 'message': 'No face detected in any frame. Retry in better lighting.'})

    try:
        db = connect_db()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO students (name, reg_no, department, class, password) VALUES (%s,%s,%s,%s,%s)",
            (name, reg_no, department, class_name, hashed_pwd)
        )
        db.commit()
        db.close()
    except Exception as e:
        return jsonify({'success': False, 'message': f'DB error: {str(e)}'})

    # Auto retrain after registration
    train_model()

    return jsonify({'success': True, 'message': f'Enrolled {name} with {saved} face photos.'})

# ================= CAMERA PAGE =================
@app.route('/camera')
def camera():
    global current_subject, current_department
    if not session.get('logged_in'):
        return redirect('/login')
    current_subject    = request.args.get('subject', 'general').strip().lower()
    current_department = request.args.get('department', 'general').strip().lower()
    return render_template("camera.html",
                           subject=current_subject,
                           department=current_department)

# ================= MARK ATTENDANCE =================
@app.route('/mark_attendance', methods=['POST'])
def mark_attendance():
    global current_subject, current_department
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not logged in'}), 401

    data       = request.get_json()
    image_data = data.get('image', '')
    current_subject    = data.get('subject', 'general').strip().lower()
    current_department = data.get('department', 'general').strip().lower()

    if not global_label_map:
        return jsonify({'success': False, 'message': 'Model not trained. Admin must run /retrain first.'})

    import base64
    header, encoded = image_data.split(',', 1)
    img_bytes = base64.b64decode(encoded)
    np_arr    = np.frombuffer(img_bytes, np.uint8)
    frame     = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None:
        return jsonify({'success': False, 'message': 'Invalid image received'})

    face_cascade = cv2.CascadeClassifier("haarcascade_frontalface_default.xml")
    gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    if len(faces) == 0:
        return jsonify({'success': False, 'message': 'No face detected. Position face clearly.'})

    db     = connect_db()
    cursor = db.cursor()
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
            cursor.execute(
                "SELECT id FROM attendance WHERE student_id=%s AND LOWER(subject)=%s AND date=%s",
                (student_id, current_subject, now.date())
            )
            if cursor.fetchone():
                skipped_names.append(name)
            else:
                cursor.execute(
                    "INSERT INTO attendance (student_id, name, department, class, subject, date, time, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (student_id, name, dept, cls, current_subject, now.date(), now.time(), "Present")
                )
                db.commit()
                marked_names.append(name)
        except Exception as e:
            print(f"DB ERROR: {e}")
    db.close()

    if marked_names:
        return jsonify({'success': True, 'message': f'✅ Marked: {", ".join(marked_names)}'})
    elif skipped_names:
        return jsonify({'success': False, 'message': f'⚠️ Already marked today: {", ".join(skipped_names)}'})
    else:
        return jsonify({'success': False, 'message': 'Face detected but not recognized. Re-register in better lighting.'})

# ================= ATTENDANCE =================
@app.route('/attendance')
def attendance():
    if not session.get('logged_in'):
        return redirect('/login')
    db = connect_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM attendance")
    data = cursor.fetchall()
    db.close()
    return render_template("attendance.html", data=data)

# ================= DASHBOARD =================
@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'):
        return redirect('/login')
    db = connect_db()
    cursor = db.cursor()
    cursor.execute("SELECT department, COUNT(*) FROM attendance GROUP BY department")
    dept_data = cursor.fetchall()
    cursor.execute("SELECT subject, COUNT(*) FROM attendance GROUP BY subject")
    subject_data = cursor.fetchall()
    db.close()
    return render_template("dashboard.html",
                           dept_data=dept_data,
                           subject_data=subject_data)

# ================= DELETE =================
@app.route('/delete/<int:id>')
def delete(id):
    if not session.get('logged_in'):
        return redirect('/login')
    db = connect_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM attendance WHERE id=%s", (id,))
    db.commit()
    db.close()
    return redirect('/attendance')

# ================= RUN =================
if __name__ == "__main__":
    app.run(debug=True)