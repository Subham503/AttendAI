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

-- Enable Row Level Security
ALTER TABLE attendance_audit_logs ENABLE ROW LEVEL SECURITY;

-- Allow inserts
CREATE POLICY "allow_insert_audit"
ON attendance_audit_logs
FOR INSERT
WITH CHECK (true);

-- Prevent updates
CREATE POLICY "no_update_audit"
ON attendance_audit_logs
FOR UPDATE
USING (false);

-- Prevent deletes
CREATE POLICY "no_delete_audit"
ON attendance_audit_logs
FOR DELETE
USING (false);