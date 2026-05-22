import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Configuration — add these to your .env file:
#   SMTP_HOST=smtp.gmail.com
#   SMTP_PORT=587
#   SMTP_USER=your_email@gmail.com
#   SMTP_PASSWORD=your_app_password   (Gmail: use an App Password, not your login)
#   ALERT_THRESHOLD=75                (optional, defaults to 75)
# ---------------------------------------------------------------------------

SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", 587))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
THRESHOLD     = int(os.getenv("ALERT_THRESHOLD", 75))


# ---------------------------------------------------------------------------
# Internal helper: send one email
# ---------------------------------------------------------------------------

def _send_email(to_address: str, subject: str, html_body: str) -> bool:
    """Send a single HTML email. Returns True on success, False on failure."""
    if not SMTP_USER or not SMTP_PASSWORD:
        print("[AttendAI] SMTP credentials not configured — skipping email.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"AttendAI <{SMTP_USER}>"
    msg["To"]      = to_address
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to_address, msg.as_string())
        print(f"[AttendAI] Alert email sent to {to_address}")
        return True
    except Exception as exc:
        print(f"[AttendAI] Failed to send email to {to_address}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Core: check attendance and fire alerts if needed
# ---------------------------------------------------------------------------

def check_and_alert(supabase_client, student_id: int, subject: str) -> None:
    """
    Called after every attendance mark. Queries Supabase for the student's
    attendance in `subject`, calculates the percentage, and sends alert
    emails to both student and faculty if it falls below THRESHOLD.

    A 24-hour cooldown is enforced via the `alert_log` table (see README
    for the CREATE TABLE statement) to avoid spamming on consecutive classes.

    Args:
        supabase_client: An initialised Supabase client (from create_client).
        student_id:      The student's primary-key integer ID.
        subject:         The subject string for the current session.
    """

    # ── 1. Fetch student details ──────────────────────────────────────────
    student_row = (
        supabase_client.table("students")
        .select("name, reg_no, department, class")
        .eq("id", student_id)
        .single()
        .execute()
    )
    if not student_row.data:
        print(f"[AttendAI] Student {student_id} not found.")
        return

    student      = student_row.data
    student_name = student["name"]
    reg_no       = student["reg_no"]
    department   = student["department"]

    # ── 2. Count total sessions and present sessions for this subject ─────
    all_records = (
        supabase_client.table("attendance")
        .select("status")
        .eq("student_id", student_id)
        .eq("subject", subject)
        .execute()
    )
    records = all_records.data or []
    total   = len(records)
    present = sum(1 for r in records if r["status"].lower() == "present")

    if total == 0:
        return  # No sessions yet — nothing to alert on.

    percentage = round((present / total) * 100, 1)

    # ── 3. Bail out if above threshold ───────────────────────────────────
    if percentage >= THRESHOLD:
        return

    # ── 4. Cooldown check — don't re-alert within 24 hours ───────────────
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    recent_alert = (
        supabase_client.table("alert_log")
        .select("id")
        .eq("student_id", student_id)
        .eq("subject", subject)
        .gte("alerted_on", yesterday)
        .execute()
    )
    if recent_alert.data:
        print(f"[AttendAI] Alert suppressed (cooldown active) for {student_name} in {subject}.")
        return

    # ── 5. Fetch faculty email for this department ────────────────────────
    faculty_row = (
        supabase_client.table("faculty")
        .select("name, email")
        .eq("department", department)
        .limit(1)
        .execute()
    )
    faculty_email = faculty_row.data[0]["email"] if faculty_row.data else None
    faculty_name  = faculty_row.data[0]["name"]  if faculty_row.data else "Faculty"

    # ── 6. Fetch student email ────────────────────────────────────────────
    student_email_row = (
        supabase_client.table("students")
        .select("email")
        .eq("id", student_id)
        .single()
        .execute()
    )
    student_email = student_email_row.data.get("email") if student_email_row.data else None

    # ── 7. Build and send emails ──────────────────────────────────────────
    warning_color = "#E24B4A" if percentage < 60 else "#BA7517"
    status_label  = "Critical" if percentage < 60 else "Warning"

    # Student email
    if student_email:
        student_subject = f"[AttendAI] Low Attendance Alert — {subject}"
        student_body = f"""
        <div style="font-family:sans-serif;max-width:560px;margin:auto;border:1px solid #e0e0e0;border-radius:8px;overflow:hidden">
          <div style="background:{warning_color};padding:20px 24px">
            <h2 style="color:#fff;margin:0;font-size:18px">Attendance {status_label}</h2>
            <p style="color:rgba(255,255,255,.85);margin:6px 0 0;font-size:14px">
              Your attendance in <strong>{subject}</strong> has dropped below {THRESHOLD}%
            </p>
          </div>
          <div style="padding:24px">
            <table style="width:100%;border-collapse:collapse;font-size:14px">
              <tr>
                <td style="padding:8px 0;color:#666">Student</td>
                <td style="padding:8px 0;font-weight:500">{student_name}</td>
              </tr>
              <tr>
                <td style="padding:8px 0;color:#666">Reg. No.</td>
                <td style="padding:8px 0">{reg_no}</td>
              </tr>
              <tr>
                <td style="padding:8px 0;color:#666">Subject</td>
                <td style="padding:8px 0">{subject}</td>
              </tr>
              <tr>
                <td style="padding:8px 0;color:#666">Classes attended</td>
                <td style="padding:8px 0">{present} / {total}</td>
              </tr>
              <tr>
                <td style="padding:8px 0;color:#666">Current %</td>
                <td style="padding:8px 0;color:{warning_color};font-weight:600">{percentage}%</td>
              </tr>
            </table>
            <hr style="border:none;border-top:1px solid #eee;margin:20px 0">
            <p style="font-size:13px;color:#666;margin:0">
              Please attend upcoming classes to avoid attendance shortfall.
              Contact your faculty if you need a condonation request.
            </p>
          </div>
          <div style="background:#f8f8f8;padding:12px 24px;font-size:12px;color:#999">
            Sent by AttendAI · NIST University Smart Campus
          </div>
        </div>
        """
        _send_email(student_email, student_subject, student_body)

    # Faculty email
    if faculty_email:
        faculty_subject = f"[AttendAI] Student Low Attendance — {student_name} in {subject}"
        faculty_body = f"""
        <div style="font-family:sans-serif;max-width:560px;margin:auto;border:1px solid #e0e0e0;border-radius:8px;overflow:hidden">
          <div style="background:{warning_color};padding:20px 24px">
            <h2 style="color:#fff;margin:0;font-size:18px">Low Attendance Report</h2>
            <p style="color:rgba(255,255,255,.85);margin:6px 0 0;font-size:14px">
              A student has fallen below {THRESHOLD}% attendance in your subject
            </p>
          </div>
          <div style="padding:24px">
            <p style="font-size:14px;color:#333;margin:0 0 16px">Dear {faculty_name},</p>
            <table style="width:100%;border-collapse:collapse;font-size:14px">
              <tr>
                <td style="padding:8px 0;color:#666">Student</td>
                <td style="padding:8px 0;font-weight:500">{student_name}</td>
              </tr>
              <tr>
                <td style="padding:8px 0;color:#666">Reg. No.</td>
                <td style="padding:8px 0">{reg_no}</td>
              </tr>
              <tr>
                <td style="padding:8px 0;color:#666">Department</td>
                <td style="padding:8px 0">{department}</td>
              </tr>
              <tr>
                <td style="padding:8px 0;color:#666">Subject</td>
                <td style="padding:8px 0">{subject}</td>
              </tr>
              <tr>
                <td style="padding:8px 0;color:#666">Attendance</td>
                <td style="padding:8px 0">{present} present / {total} total</td>
              </tr>
              <tr>
                <td style="padding:8px 0;color:#666">Percentage</td>
                <td style="padding:8px 0;color:{warning_color};font-weight:600">{percentage}%</td>
              </tr>
            </table>
            <hr style="border:none;border-top:1px solid #eee;margin:20px 0">
            <p style="font-size:13px;color:#666;margin:0">
              This alert was automatically generated. The student has also been notified.
            </p>
          </div>
          <div style="background:#f8f8f8;padding:12px 24px;font-size:12px;color:#999">
            Sent by AttendAI · NIST University Smart Campus
          </div>
        </div>
        """
        _send_email(faculty_email, faculty_subject, faculty_body)

    # ── 8. Log this alert to prevent re-sending within 24 hours ──────────
    supabase_client.table("alert_log").insert({
        "student_id":  student_id,
        "subject":     subject,
        "percentage":  percentage,
        "alerted_on":  date.today().isoformat(),
    }).execute()

    print(f"[AttendAI] Low attendance alert fired: {student_name} | {subject} | {percentage}%")