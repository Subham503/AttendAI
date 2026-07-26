-- Migration: Immutable Attendance Audit Logs

CREATE TABLE IF NOT EXISTS attendance_audit_logs (
    id SERIAL PRIMARY KEY,

    attendance_id INT,

    user_id TEXT NOT NULL,
    user_role VARCHAR(20) NOT NULL,

    action VARCHAR(20) NOT NULL,

    previous_values JSONB,
    updated_values JSONB,

    session_id TEXT,

    ip_address TEXT,

    created_at TIMESTAMP DEFAULT NOW()
);