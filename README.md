# AttendAI 🎓
> AI-powered face recognition attendance system built for the NIST University Smart Campus initiative.

![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python)
![Flask](https://img.shields.io/badge/Flask-3.x-black?style=flat-square&logo=flask)
![OpenCV](https://img.shields.io/badge/OpenCV-LBPH-green?style=flat-square&logo=opencv)
![Supabase](https://img.shields.io/badge/Database-Supabase-3ECF8E?style=flat-square&logo=supabase)
![MediaPipe](https://img.shields.io/badge/Liveness-MediaPipe-orange?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)

---

## 📌 Overview

AttendAI automates student attendance using real-time face recognition. Faculty start a class session, the system auto-scans faces continuously via browser webcam, and attendance is marked instantly — no manual effort, no proxy attendance possible.

Built as part of the **NIST University AI-Enabled Smart Campus Project** under the Department of Computer Science & Engineering.

---

## ✨ Features

- 🎥 **Browser-based webcam capture** — no server camera required, works on any deployment
- 👁️ **Liveness detection** — MediaPipe blink detection prevents photo/screen spoofing
- 🔄 **Auto-continuous scanning** — scans every 3 seconds, marks entire class without manual clicks
- 👤 **20-frame face registration** — captures multiple angles for better recognition accuracy
- 🔐 **bcrypt password hashing** — secure auth for Admin, Faculty, and Student roles
- ⚡ **Optimized model loading** — LBPH model loads once at startup, not on every scan
- 🔁 **Auto-retrain** — model retrains automatically after every new student registration
- 📊 **Analytics dashboard** — attendance stats by department and subject
- 📥 **CSV export** — download attendance reports as Excel-compatible CSV
- 🗂️ **Role-based access** — Admin, Faculty, Student with separate dashboards
- ☁️ **Supabase cloud database** — no local DB setup, works from anywhere
- 🌐 **Environment-based config** — no hardcoded credentials, uses `.env`

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, Flask 3.x |
| Face Detection | OpenCV (Haar Cascade) |
| Face Recognition | LBPH (Local Binary Patterns Histograms) |
| Liveness Detection | MediaPipe Face Mesh (Eye Aspect Ratio) |
| Frontend | HTML, CSS, JavaScript (getUserMedia API) |
| Database | Supabase (PostgreSQL) |
| Auth | bcrypt, Flask Session |
| Config | python-dotenv |

---

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- Webcam
- Supabase account (free at supabase.com)

### Installation

```bash
# Clone the repo
git clone https://github.com/Subham503/AttendAI.git
cd AttendAI

# Install dependencies
pip install flask opencv-contrib-python bcrypt numpy python-dotenv supabase
```

### Environment Variables

Create a `.env` file in the root directory:

```env
SUPABASE_URL=your_supabase_project_url
SUPABASE_KEY=your_supabase_service_role_key
SECRET_KEY=your_flask_secret_key
```

### Database Setup

Go to Supabase Dashboard → SQL Editor → Run:

```sql
CREATE TABLE students (
  id SERIAL PRIMARY KEY,
  name VARCHAR(100),
  reg_no VARCHAR(50) UNIQUE,
  department VARCHAR(50),
  class VARCHAR(50),
  password VARCHAR(255)
);

CREATE TABLE attendance (
  id SERIAL PRIMARY KEY,
  student_id INT REFERENCES students(id),
  name VARCHAR(100),
  department VARCHAR(50),
  class VARCHAR(50),
  subject VARCHAR(100),
  date DATE,
  time TIME,
  status VARCHAR(20)
);

CREATE TABLE admins (
  id SERIAL PRIMARY KEY,
  username VARCHAR(50),
  password VARCHAR(255)
);

CREATE TABLE faculty (
  id SERIAL PRIMARY KEY,
  faculty_id VARCHAR(50),
  name VARCHAR(100),
  password VARCHAR(255)
);
```

### Run

```bash
python app.py
```

Visit `http://localhost:5000`

---

## 📁 Project Structure

```
AttendAI/
├── app.py                              # Main Flask application
├── face_utils.py                       # DeepFace utility (future upgrade)
├── train.py                            # Standalone training script
├── haarcascade_frontalface_default.xml # Face detection model
├── images/                             # Registered face images (local)
├── trainer.yml                         # Trained LBPH model (auto-generated)
├── labels.pickle                       # Label map (auto-generated)
├── .env                                # Environment variables (not committed)
└── templates/
    ├── index.html                      # Home dashboard
    ├── login.html                      # Multi-role login
    ├── register.html                   # Browser webcam 20-frame registration
    ├── camera.html                     # Auto-continuous attendance scanner
    ├── attendance.html                 # Records + CSV export
    ├── dashboard.html                  # Analytics charts
    └── class_session.html             # Session setup
```

---

## 🔄 How It Works

```
Register Student
      │
      ▼
Browser opens webcam → captures 20 frames
      │
      ▼
Flask detects faces → saves images → auto-retrains LBPH model
      │
      ▼
Faculty starts class session (subject + department)
      │
      ▼
Camera page → MediaPipe blink check (liveness verified)
      │
      ▼
Auto-scans every 3 seconds → face detected → LBPH predicts identity
      │
      ▼
Attendance marked in Supabase → live log shown on screen
      │
      ▼
Export CSV → downloadable attendance report
```

---

## 🗺️ Project Workflow

[![View Flowchart](https://img.shields.io/badge/Figma-View%20Flowchart-purple?logo=figma)](https://www.figma.com/board/0sZlZNlQV1VcEwg6zwWtAL/AttendAI-%E2%80%94-Project-Workflow?node-id=0-1&p=f&t=MwvKWv2TLTeE7Iut-0)

---

## 👁️ Liveness Detection

AttendAI uses **MediaPipe Face Mesh** to compute the **Eye Aspect Ratio (EAR)** in real time. When EAR drops below threshold for 2+ consecutive frames, a blink is detected — confirming the face is real and not a photo or screen.

```
EAR = (vertical distances) / (horizontal distance)
Open eye → EAR ≈ 0.25–0.30
Blink    → EAR < 0.20 → LIVENESS CONFIRMED ✅
Photo    → EAR never drops → REJECTED ❌
```

---

## 🔐 Roles

| Role | Access |
|---|---|
| **Admin** | All records, retrain model, manage sessions, export CSV |
| **Faculty** | Start sessions, mark attendance, view records |
| **Student** | View own attendance history |

---

## 📍 Roadmap

- [x] Browser-based webcam capture
- [x] Liveness detection (anti-spoofing)
- [x] Auto-continuous scanning
- [x] Supabase cloud database
- [x] CSV export
- [x] bcrypt auth
- [ ] QR-based student self-checkin
- [ ] DeepFace / FaceNet upgrade
- [ ] Low attendance email alerts
- [ ] React Native mobile app

---

## 👨‍💻 Author

**Subham Sahu**
- GitHub: [@Subham503](https://github.com/Subham503)
- Email: sahusubham38632@gmail.com

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

> Built with ❤️ for NIST University Smart Campus Initiative