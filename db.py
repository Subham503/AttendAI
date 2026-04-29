import mysql.connector

def connect_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="root123",  # change if needed
        database="smart_attendance"
    )