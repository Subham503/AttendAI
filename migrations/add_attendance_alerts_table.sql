-- Run this in your Supabase SQL editor
-- Migration: Add attendance_alerts table for low attendance email alerts

CREATE TABLE IF NOT EXISTS attendance_alerts (
    id SERIAL PRIMARY KEY,
    student_id INT REFERENCES students(id),
    subject VARCHAR(100),
    percentage FLOAT,
    sent_at TIMESTAMP DEFAULT NOW()
);