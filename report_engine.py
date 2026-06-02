from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from datetime import datetime
import io

def generate_attendance_pdf(supabase_client, subject=None, start_date=None, end_date=None):

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)

    elements = []
    styles = getSampleStyleSheet()

    title = "Attendance Report"
    elements.append(Paragraph(title, styles['Title']))
    elements.append(Spacer(1, 12))

    query = supabase_client.table('attendance').select('*')

    if subject:
        query = query.eq('subject', subject)
    if start_date:
        query = query.gte('date', start_date)
    if end_date:
        query = query.lte('date', end_date)

    result = query.execute()
    data = result.data or []

    table_data = [
        ["ID", "Name", "Department", "Class", "Subject", "Date", "Time", "Status"]
    ]

    for r in data:
        table_data.append([
            r.get("student_id"),
            r.get("name"),
            r.get("department"),
            r.get("class"),
            r.get("subject"),
            r.get("date"),
            r.get("time"),
            r.get("status"),
        ])

    table = Table(table_data)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.grey),
        ("TEXTCOLOR", (0,0), (-1,0), colors.whitesmoke),
        ("GRID", (0,0), (-1,-1), 0.5, colors.black),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("PADDING", (0,0), (-1,-1), 5),
    ]))

    elements.append(table)

    doc.build(elements)
    buffer.seek(0)

    return buffer.getvalue()