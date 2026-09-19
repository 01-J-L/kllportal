-- =====================================================
-- KLL Portal - Full Database Setup for PythonAnywhere
-- Run this in your PythonAnywhere MySQL console
-- =====================================================

-- 1. USERS TABLE (must exist before password_reset_tokens)
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    phone_number VARCHAR(20),
    role VARCHAR(20) DEFAULT 'STUDENT',
    status VARCHAR(20) DEFAULT 'Active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. PASSWORD RESET TOKENS (depends on users)
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    token VARCHAR(64) NOT NULL UNIQUE,
    expires_at DATETIME NOT NULL,
    used TINYINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 3. SITE SETTINGS
CREATE TABLE IF NOT EXISTS site_settings (
    setting_key VARCHAR(255) PRIMARY KEY,
    setting_value TEXT
);

-- 4. COURSES
CREATE TABLE IF NOT EXISTS courses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    course_code VARCHAR(50) NOT NULL,
    course_name VARCHAR(255) NOT NULL,
    description TEXT,
    credits DECIMAL(5,2) DEFAULT 0.00,
    curriculum_description TEXT,
    objectives TEXT,
    outcomes TEXT,
    is_active TINYINT DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. NEWS
CREATE TABLE IF NOT EXISTS news (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    content TEXT,
    image_url VARCHAR(255),
    author_name VARCHAR(255) DEFAULT 'Admin',
    link_url VARCHAR(255),
    is_active TINYINT DEFAULT 1,
    category VARCHAR(50) DEFAULT 'General',
    status VARCHAR(20) DEFAULT 'published',
    publish_date DATETIME NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. EVENTS
CREATE TABLE IF NOT EXISTS events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    event_date DATETIME,
    image_url VARCHAR(255),
    link_url VARCHAR(255),
    is_active TINYINT DEFAULT 1,
    category VARCHAR(50) DEFAULT 'Campus Events',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 7. CLUBS
CREATE TABLE IF NOT EXISTS clubs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    details TEXT,
    image_url VARCHAR(255),
    link_url VARCHAR(255),
    is_active TINYINT DEFAULT 1,
    category VARCHAR(50) DEFAULT 'Student Organization',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 8. VIRTUAL TOURS
CREATE TABLE IF NOT EXISTS virtual_tours (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    video_id VARCHAR(255),
    file_url VARCHAR(255),
    tour_type VARCHAR(20) DEFAULT 'YOUTUBE',
    is_active TINYINT DEFAULT 1,
    category VARCHAR(50) DEFAULT 'Facilities',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 9. STUDENT HANDBOOKS
CREATE TABLE IF NOT EXISTS student_handbooks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    file_url VARCHAR(255),
    file_size VARCHAR(50),
    is_active TINYINT DEFAULT 1,
    category VARCHAR(50) DEFAULT 'General',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 10. ENROLLMENTS
CREATE TABLE IF NOT EXISTS enrollments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT NOT NULL,
    course_id INT NOT NULL,
    enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
);

-- 11. ACADEMIC CALENDAR
CREATE TABLE IF NOT EXISTS academic_calendar (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    start_date DATE,
    end_date DATE,
    is_active TINYINT DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 12. ABOUT SECTIONS
CREATE TABLE IF NOT EXISTS about_sections (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    body TEXT,
    is_active TINYINT DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 13. INQUIRIES
CREATE TABLE IF NOT EXISTS inquiries (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(20),
    subject VARCHAR(255),
    message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 14. EMERGENCY ADVISORIES
CREATE TABLE IF NOT EXISTS emergency_advisories (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    advisory_type VARCHAR(30) DEFAULT 'info',
    is_active TINYINT DEFAULT 1,
    expires_at DATETIME NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 15. MEDIA LIBRARY
CREATE TABLE IF NOT EXISTS media_library (
    id INT AUTO_INCREMENT PRIMARY KEY,
    filename VARCHAR(255) NOT NULL,
    original_name VARCHAR(255),
    file_type VARCHAR(50),
    file_size BIGINT DEFAULT 0,
    uploaded_by INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL
);

-- =====================================================
-- Create a default SUPER_ADMIN user
-- Password: admin123 (change this immediately!)
-- =====================================================
INSERT IGNORE INTO users (name, email, password_hash, phone_number, role, status)
VALUES (
    'Super Admin',
    'admin@example.com',
    'pbkdf2:sha256:600000$placeholder$placeholder',
    '09171234567',
    'SUPER_ADMIN',
    'Active'
);
