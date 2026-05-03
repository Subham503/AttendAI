from flask import Flask, render_template, request, redirect, Response, jsonify, session
import cv2
from datetime import datetime
from supabase import create_client
import os
import numpy as np
import bcrypt
import pickle
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "smartattend_secret_2024")

supabase_client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

current_subject = "general"
current_department = "general"

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

    # POST — comes as JSON from the login form JS or form data
    data = request.get_json(force=True, silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    role     = data.get('role', 'admin')

    if role == 'admin':
        result = supabase_client.table('admins').select('password').eq('username', username).execute()
        user = result.data[0] if result.data else None
        if user and bcrypt.checkpw(password.encode(), user['password'].encode()):
            session['logged_in'] = True
            session['role']      = 'admin'
            session['name']      = username
            return jsonify({'success': True, 'redirect': '/'})
        return jsonify({'success': False, 'message': 'Invalid admin credentials.'})

    elif role == 'faculty':
        result = supabase_client.table('faculty').select('password, name').eq('faculty_id', username).execute()
        user = result.data[0] if result.data else None
        if user and bcrypt.checkpw(password.encode(), user['password'].encode()):
            session['logged_in'] = True
            session['role']      = 'faculty'
            session['name']      = user['name'] if user['name'] else username
            return jsonify({'success': True, 'redirect': '/'})
        return jsonify({'success': False, 'message': 'Invalid faculty credentials.'})

    elif role == 'student':
        result = supabase_client.table('students').select('password, name').eq('reg_no', username).execute()
        user = result.data[0] if result.data else None
        if user and bcrypt.checkpw(password.encode(), user['password'].encode()):
            session['logged_in'] = True
            session['role']      = 'student'
            session['name']      = user['name']
            session['reg_no']    = username
            return jsonify({'success': True, 'redirect': '/'})
        return jsonify({'success': False, 'message': 'Invalid student credentials.'})

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
    
    student_res = supabase_client.table('students').select('id').eq('reg_no', reg_no).execute()
    student_id = student_res.data[0]['id'] if student_res.data else None
    
    if student_id:
        records_res = supabase_client.table('attendance').select('subject, date, time, status').eq('student_id', student_id).order('date', desc=True).order('time', desc=True).execute()
        records = [(r['subject'], r['date'], r['time'], r['status']) for r in (records_res.data or [])]
        
        all_att_res = supabase_client.table('attendance').select('subject').eq('student_id', student_id).execute()
        stats = {}
        for r in (all_att_res.data or []):
            stats[r['subject']] = stats.get(r['subject'], 0) + 1
        subject_stats = [(subj, count) for subj, count in stats.items()]
    else:
        records = []
        subject_stats = []
        
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
    data = request.get_json(force=True, silent=True) or {}
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
        supabase_client.table('students').insert({
            'name': name,
            'reg_no': reg_no,
            'department': department,
            'class': class_name,
            'password': hashed_pwd.decode('utf-8')
        }).execute()
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
            existing = supabase_client.table('attendance').select('id').eq('student_id', student_id).ilike('subject', current_subject).eq('date', str(now.date())).execute()
            if existing.data:
                skipped_names.append(name)
            else:
                supabase_client.table('attendance').insert({
                    'student_id': student_id,
                    'name': name,
                    'department': dept,
                    'class': cls,
                    'subject': current_subject,
                    'date': str(now.date()),
                    'time': str(now.time()),
                    'status': 'Present'
                }).execute()
                marked_names.append(name)
        except Exception as e:
            print(f"DB ERROR: {e}")

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
    
    result = supabase_client.table('attendance').select('*').execute()
    data = []
    for r in (result.data or []):
        data.append((
            r.get('id'), r.get('student_id'), r.get('name'), r.get('department'), 
            r.get('class'), r.get('subject'), r.get('date'), r.get('time'), r.get('status')
        ))
        
    return render_template("attendance.html", data=data)

# ================= DASHBOARD =================
@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'):
        return redirect('/login')
    
    result = supabase_client.table('attendance').select('department, subject').execute()
    dept_counts = {}
    subj_counts = {}
    for r in (result.data or []):
        dept = r.get('department')
        subj = r.get('subject')
        dept_counts[dept] = dept_counts.get(dept, 0) + 1
        subj_counts[subj] = subj_counts.get(subj, 0) + 1
        
    dept_data = [(k, v) for k, v in dept_counts.items()]
    subject_data = [(k, v) for k, v in subj_counts.items()]
    
    return render_template("dashboard.html",
                           dept_data=dept_data,
                           subject_data=subject_data)

# ================= DELETE =================
@app.route('/delete/<int:id>')
def delete(id):
    if not session.get('logged_in'):
        return redirect('/login')
    
    supabase_client.table('attendance').delete().eq('id', id).execute()
    return redirect('/attendance')

# ================= RUN =================
if __name__ == "__main__":
    app.run(debug=True)