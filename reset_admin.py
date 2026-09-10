# reset_admin.py
from app import app, mysql
from werkzeug.security import generate_password_hash

with app.app_context():
    cur = mysql.connection.cursor()
    
    # 1. Reset Admin (Password: admin123)
    admin_pw = generate_password_hash('admin123')
    cur.execute("""
        INSERT INTO users (name, email, password_hash, role, status)
        VALUES ('System Admin', 'admin@admin.com', %s, 'SUPER_ADMIN', 'ACTIVE')
        ON DUPLICATE KEY UPDATE password_hash=%s, role='SUPER_ADMIN', status='ACTIVE'
    """, (admin_pw, admin_pw))

    # 2. Reset Student (Password: student123)
    student_pw = generate_password_hash('student123')
    cur.execute("""
        INSERT INTO users (name, email, password_hash, role, status)
        VALUES ('Maria Cristina G. Magnaye', 'student@student.com', %s, 'STUDENT', 'ACTIVE')
        ON DUPLICATE KEY UPDATE password_hash=%s, role='STUDENT', status='ACTIVE'
    """, (student_pw, student_pw))

    mysql.connection.commit()
    cur.close()
    print("Successfully created/reset accounts:")
    print("Admin: admin@admin.com | admin123")
    print("Student: student@student.com | student123") 