# AttendAI 🎓
> AI-powered face recognition attendance system built for the NIST University Smart Campus initiative.

![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python)
![Flask](https://img.shields.io/badge/Flask-2.x-black?style=flat-square&logo=flask)
![OpenCV](https://img.shields.io/badge/OpenCV-LBPH-green?style=flat-square&logo=opencv)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)

---

## 📌 Overview

AttendAI automates student attendance using real-time face recognition. Faculty start a class session, the system scans faces via browser webcam, and attendance is marked instantly — no manual effort, no proxy attendance.

Built as part of the **NIST University AI-Enabled Smart Campus Project** under the Department of Computer Science & Engineering.

---

## ✨ Features

- 🎥 **Browser-based webcam capture** — no server camera required, works on any deployment
- 👤 **20-frame face registration** — captures multiple angles for better recognition accuracy
- 🔐 **bcrypt password hashing** — secure auth for Admin, Faculty, and Student roles
- ⚡ **Optimized model loading** — LBPH model loads once at startup, not on every scan
- 🔄 **Auto-retrain** — model retrains automatically after every new student registration
- 📊 **Analytics dashboard** — attendance stats by department and subject
- 🗂️ **Role-based access** — Admin, Faculty, Student with separate dashboards
- 🌐 **Environment-based config** — no hardcoded credentials, uses `.env`

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, Flask |
| Face Detection | OpenCV (Haar Cascade) |
| Face Recognition | LBPH (Local Binary Patterns Histograms) |
| Frontend | HTML, CSS, JavaScript (getUserMedia API) |
| Database | MySQL |
| Auth | bcrypt, Flask Session |
| Config | python-dotenv |

---

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- MySQL server running locally
- Webcam

### Installation

```bash
# Clone the repo
git clone https://github.com/Subham503/AttendAI.git
cd AttendAI

# Install dependencies
pip install flask opencv-contrib-python mysql-connector-python bcrypt numpy python-dotenv

# Set up environment variables
cp .env.example .env
# Edit .env with your MySQL credentials
```

### Environment Variables

Create a `.env` file in the root directory:

```env
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=smart_attendance
SECRET_KEY=your_secret_key
```

### Database Setup

```sql
CREATE DATABASE smart_attendance;

USE smart_attendance;

CREATE TABLE students (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100),
  reg_no VARCHAR(50) UNIQUE,
  department VARCHAR(50),
  class VARCHAR(50),
  password VARCHAR(255)
);

CREATE TABLE attendance (
  id INT AUTO_INCREMENT PRIMARY KEY,
  student_id INT,
  name VARCHAR(100),
  department VARCHAR(50),
  class VARCHAR(50),
  subject VARCHAR(100),
  date DATE,
  time TIME,
  status VARCHAR(20),
  FOREIGN KEY (student_id) REFERENCES students(id)
);

CREATE TABLE admins (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(50),
  password VARCHAR(255)
);

CREATE TABLE faculty (
  id INT AUTO_INCREMENT PRIMARY KEY,
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
├── app.py                          # Main Flask application
├── face_utils.py                   # DeepFace utility (future upgrade)
├── train.py                        # Standalone training script
├── haarcascade_frontalface_default.xml
├── images/                         # Registered face images
├── trainer.yml                     # Trained LBPH model (auto-generated)
├── labels.pickle                   # Label map (auto-generated)
├── .env                            # Environment variables (not committed)
├── .env.example                    # Template for env setup
└── templates/
    ├── index.html
    ├── login.html
    ├── register.html               # Browser webcam 20-frame capture
    ├── camera.html                 # Browser-based attendance scan
    ├── attendance.html
    ├── dashboard.html
    ├── class_session.html
    └── student_dashboard.html
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
Flask saves face images → auto-retrains LBPH model
      │
      ▼
Faculty starts class session (subject + department)
      │
      ▼
Camera page opens → browser captures frame → sends to Flask
      │
      ▼
Flask detects face → LBPH predicts identity
      │
      ▼
Attendance marked in MySQL → redirect to records
```

---

## 🔐 Roles

| Role | Access |
|---|---|
| **Admin** | All records, retrain model, manage sessions |
| **Faculty** | Start sessions, mark attendance |
| **Student** | View own attendance history |

---

## 🗺️ Roadmap

- [ ] Liveness detection (anti-spoofing via blink detection)
- [ ] DeepFace / FaceNet upgrade for better accuracy
- [ ] Supabase migration (replace MySQL)
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