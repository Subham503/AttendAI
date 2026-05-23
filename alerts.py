from flask_mail import Mail, Message
from datetime import datetime, timedelta

mail = Mail()

def init_mail(app):
    """Initialize Flask-Mail with app config"""
    app.config['MAIL_SERVER'] = 'smtp.gmail.com'
    app.config['MAIL_PORT'] = 587
    app.config['MAIL_USE_TLS'] = True
    app.config['MAIL_USERNAME'] = app.config.get('MAIL_USERNAME')
    app.config['MAIL_PASSWORD'] = app.config.get('MAIL_PASSWORD')
    app.config['MAIL_DEFAULT_SENDER'] = app.config.get('MAIL_USERNAME')
    mail.init_app(app)

def calculate_attendance_percentage(supabase, student_id, subject=None):
    """Calculate attendance percentage for a student"""
    query = supabase.table('attendance').select('*').eq('student_id', student_id)
    if subject:
        query = query.eq('subject', subject)
    
    records = query.execute()
    
    if not records.data:
        return {}
    
    subject_stats = {}
    for record in records.data:
        subj = record['subject']
        if subj not in subject_stats:
            subject_stats[subj] = {'present': 0, 'total': 0}
        subject_stats[subj]['total'] += 1
        if record['status'] == 'Present':
            subject_stats[subj]['present'] += 1
    
    result = {}
    for subj, stats in subject_stats.items():
        percentage = (stats['present'] / stats['total']) * 100
        result[subj] = round(percentage, 2)
    
    return result

def has_alert_been_sent(supabase, student_id, subject):
    """Check if alert was already sent this week"""
    week_ago = (datetime.now() - timedelta(days=7)).date().isoformat()
    
    result = supabase.table('attendance_alerts').select('*')\
        .eq('student_id', student_id)\
        .eq('subject', subject)\
        .gte('sent_at', week_ago)\
        .execute()
    
    return len(result.data) > 0

def log_alert(supabase, student_id, subject, percentage):
    """Log that an alert was sent"""
    supabase.table('attendance_alerts').insert({
        'student_id': student_id,
        'subject': subject,
        'percentage': percentage,
        'sent_at': datetime.now().isoformat()
    }).execute()

def send_low_attendance_alert(app, supabase, student, threshold=75.0):
    """
    Check student attendance and send email alert if below threshold.
    Returns list of subjects for which alerts were sent.
    """
    alerts_sent = []
    
    subject_percentages = calculate_attendance_percentage(
        supabase, student['id']
    )
    
    with app.app_context():
        for subject, percentage in subject_percentages.items():
            if percentage < threshold:
                # Skip if already alerted this week
                if has_alert_been_sent(supabase, student['id'], subject):
                    continue
                
                try:
                    msg = Message(
                        subject=f"⚠️ Low Attendance Alert – {subject}",
                        recipients=[student['email']]
                    )
                    msg.html = render_alert_email(
                        student['name'], subject, percentage, threshold
                    )
                    mail.send(msg)
                    log_alert(supabase, student['id'], subject, percentage)
                    alerts_sent.append(subject)
                except Exception as e:
                    print(f"Failed to send alert to {student['email']}: {e}")
    
    return alerts_sent

def render_alert_email(name, subject, percentage, threshold):
    """Generate HTML email body"""
    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: auto;">
        <div style="background:#ff4444; padding:20px; border-radius:8px 8px 0 0;">
            <h2 style="color:white; margin:0;">⚠️ Low Attendance Warning</h2>
        </div>
        <div style="background:#f9f9f9; padding:20px; border-radius:0 0 8px 8px;">
            <p>Dear <strong>{name}</strong>,</p>
            <p>Your attendance in <strong>{subject}</strong> has dropped below 
            the required threshold.</p>
            <div style="background:#fff3cd; padding:15px; border-radius:5px; 
                        border-left:4px solid #ffc107; margin:15px 0;">
                <p style="margin:0;"><strong>Current Attendance:</strong> 
                <span style="color:#dc3545; font-size:1.2em;">{percentage}%</span></p>
                <p style="margin:5px 0 0;"><strong>Required:</strong> {threshold}%</p>
            </div>
            <p>Please ensure regular attendance to avoid academic consequences.</p>
            <p>Login to <strong>AttendAI</strong> to view your detailed 
            attendance records.</p>
            <hr>
            <p style="color:#888; font-size:0.85em;">
            This is an automated alert from AttendAI – NIST University Smart Campus
            </p>
        </div>
    </body>
    </html>
    """