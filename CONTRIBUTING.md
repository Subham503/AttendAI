# Contributing to AttendAI 🎓

Thanks for your interest in contributing to AttendAI! This guide will help you get started.

---

## 📋 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [How to Contribute](#how-to-contribute)
- [Issue Labels](#issue-labels)
- [Pull Request Process](#pull-request-process)
- [Commit Message Format](#commit-message-format)
- [Project Structure](#project-structure)

---

## Code of Conduct

Please read our [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before contributing.

---

## Getting Started

### 1. Fork & Clone

```bash
git clone https://github.com/<your-username>/AttendAI.git
cd AttendAI
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Setup Environment

Create a `.env` file in root:

```
SUPABASE_URL=your_supabase_project_url
SUPABASE_KEY=your_supabase_service_role_key
SECRET_KEY=your_flask_secret_key
```

### 4. Setup Supabase Database

Run the SQL from the README in your Supabase SQL Editor.

### 5. Run the App

```bash
python app.py
```

Visit `http://localhost:5000`

---

## How to Contribute

### Find an Issue

- Browse [open issues](https://github.com/Subham503/AttendAI/issues)
- Look for `good first issue` label if you're new
- Comment **"I'd like to work on this"** and wait for assignment
- **Do NOT submit a PR for an unassigned issue**

### Create a Branch

```bash
git checkout -b fix/issue-title
# or
git checkout -b feat/feature-name
```

### Make Changes & Test

- Test your changes locally before submitting
- Make sure the app runs without errors
- Keep changes focused — one issue per PR

---

## Issue Labels

| Label | Points | Description |
|-------|--------|-------------|
| `level-1` | 3 pts | Small fixes — UI, typos, README updates |
| `level-2` | 5 pts | Medium features — new routes, UI components |
| `level-3` | 10 pts | Complex features — new modules, integrations |
| `bug` | — | Something is broken |
| `enhancement` | — | New feature request |
| `good first issue` | — | Great for beginners |
| `documentation` | — | Docs improvement |

---

## Pull Request Process

1. Make sure your branch is up to date with `main`
2. Fill out the PR template completely
3. Link the issue number: `Closes #<issue-number>`
4. Wait for review — don't merge your own PR
5. Address review comments promptly

### PR Title Format

```
feat: add dark mode to dashboard
fix: resolve CSV export bug on Windows
docs: update setup instructions
refactor: clean up face_utils.py
```

---

## Commit Message Format

```
type: short description

Types: feat | fix | docs | style | refactor | test | chore
```

Examples:
```
feat: add QR-based student self-checkin
fix: mediapipe blink threshold on low-light webcam
docs: add env setup instructions to README
```

---

## Project Structure

```
AttendAI/
├── app.py              # Main Flask app — routes & logic
├── face_utils.py       # Face recognition utilities
├── train.py            # LBPH model training script
├── db.py               # Supabase database helpers
├── templates/          # HTML templates (Jinja2)
├── images/             # Registered face images (gitignored)
├── trainer.yml         # Trained model (gitignored)
├── labels.pickle       # Label map (gitignored)
└── .env                # Environment variables (gitignored)
```

---

## Questions?

Open a [Discussion](https://github.com/Subham503/AttendAI/discussions) or ping the project admin on the NSoC Discord.

**Happy Contributing! 🚀**
