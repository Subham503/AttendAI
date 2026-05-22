import io
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch

def generate_attendance_pdf(supabase_client, subject=None, start_date=None, end_date=None):
    """
    Generates a PDF report for attendance.
    If subject is provided, filters by subject.
    If start_date and end_date are provided, filters by that date range (inclusive).
    """
    query = supabase_client.table('attendance').select('*')
    
    if subject:
        query = query.ilike('subject', subject)
        
    if start_date:
        query = query.gte('date', start_date)
        
    if end_date:
        query = query.lte('date', end_date)
        
    result = query.execute()
    records = result.data or []
    
    # Calculate statistics
    # A session is uniquely identified by its date (assuming 1 class per day per subject)
    # Total sessions = number of unique dates for the subject
    unique_dates = set(r.get('date') for r in records if r.get('date'))
    total_sessions = len(unique_dates)
    
    # Group by student
    # student_id -> {'name': name, 'reg_no': reg_no, 'dept': dept, 'attended_dates': set()}
    student_stats = {}
    
    # We need to map student_id to their details. 
    # The attendance table has student_id, name, department, class. (It doesn't have reg_no directly).
    for r in records:
        sid = r.get('student_id')
        if sid not in student_stats:
            student_stats[sid] = {
                'name': r.get('name', 'Unknown'),
                'department': r.get('department', ''),
                'class': r.get('class', ''),
                'attended_dates': set()
            }
        student_stats[sid]['attended_dates'].add(r.get('date'))
        
    # Build PDF buffer
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    elements = []
    
    styles = getSampleStyleSheet()
    title_style = styles['Heading1']
    title_style.alignment = 1 # Center
    
    subtitle_style = styles['Normal']
    subtitle_style.alignment = 1
    subtitle_style.textColor = colors.dimgrey
    
    # Title
    elements.append(Paragraph("SmartAttend - Attendance Report", title_style))
    elements.append(Spacer(1, 0.1 * inch))
    
    # Subtitle
    date_range_str = f"Date Range: {start_date or 'All Time'} to {end_date or 'All Time'}"
    subj_str = f"Subject: {subject.title() if subject else 'All Subjects'}"
    elements.append(Paragraph(f"{subj_str} | {date_range_str}", subtitle_style))
    elements.append(Paragraph(f"Total Sessions Held: {total_sessions}", subtitle_style))
    elements.append(Spacer(1, 0.3 * inch))
    
    # Table Data
    data = [["Student Name", "Department", "Class", "Classes Attended", "Attendance %"]]
    
    # For highlighting rows where attendance < 75%
    table_style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#00fff7')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('ALIGN', (0, 1), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ])
    
    row_idx = 1
    for sid, stats in student_stats.items():
        attended = len(stats['attended_dates'])
        percentage = (attended / total_sessions * 100) if total_sessions > 0 else 0
        
        data.append([
            stats['name'],
            stats['department'],
            stats['class'],
            f"{attended} / {total_sessions}",
            f"{percentage:.1f}%"
        ])
        
        # Highlight if below 75%
        if percentage < 75.0:
            # We apply a light red background to this row
            table_style.add('BACKGROUND', (0, row_idx), (-1, row_idx), colors.HexColor('#ffcccc'))
            table_style.add('TEXTCOLOR', (0, row_idx), (-1, row_idx), colors.HexColor('#990000'))
            
        row_idx += 1
        
    if not student_stats:
        elements.append(Paragraph("No attendance records found for this criteria.", styles['Normal']))
    else:
        # Create Table
        col_widths = [150, 100, 80, 100, 80]
        t = Table(data, colWidths=col_widths)
        t.setStyle(table_style)
        elements.append(t)
        
    doc.build(elements)
    
    pdf_value = buffer.getvalue()
    buffer.close()
    return pdf_value
