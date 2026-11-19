-- =====================================================================
-- KLL Portal - Enrollment & Prospectus upgrade (SAFE / IDEMPOTENT)
--
--   * Extends the EXISTING `enrollments` table (no duplicate table).
--   * Creates the new `course_documents` table (prospectus / curriculum).
--   * Never drops tables, never deletes students / enrollments / courses.
--   * Safe to run more than once (every step checks information_schema).
--
-- Works on MySQL 5.7+/8.x and MariaDB 10.x.
-- Run it with:   mysql -u <user> -p kll_portal_db < migrations/enrollment_prospectus_upgrade.sql
-- (The Flask app also runs this file automatically on start-up - see
--  run_sql_migration() in app.py - so running it by hand is optional.)
--
-- File format rules (the app's loader depends on them):
--   * one statement per ';' terminator, no stored procedures / DELIMITER,
--   * no ';' inside string literals or comments.
--
-- Modelling note: "is_current" is 1 for a student's CURRENT program and NULL
-- for historical rows. A UNIQUE index on (student_id, is_current) therefore
-- allows any number of historical rows but only ONE current row per student
-- (MySQL treats NULLs as distinct in unique indexes). Same idea is used for
-- course_documents (one current prospectus per course).
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. enrollments: new columns (each added only if missing)
-- ---------------------------------------------------------------------
SET @s = IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'enrollments' AND COLUMN_NAME = 'enrollment_status'),
            'SELECT 1',
            'ALTER TABLE enrollments ADD COLUMN enrollment_status VARCHAR(20) NULL');
PREPARE stmt FROM @s;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @s = IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'enrollments' AND COLUMN_NAME = 'is_current'),
            'SELECT 1',
            'ALTER TABLE enrollments ADD COLUMN is_current TINYINT NULL');
PREPARE stmt FROM @s;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @s = IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'enrollments' AND COLUMN_NAME = 'academic_year'),
            'SELECT 1',
            'ALTER TABLE enrollments ADD COLUMN academic_year VARCHAR(20) NULL');
PREPARE stmt FROM @s;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @s = IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'enrollments' AND COLUMN_NAME = 'semester'),
            'SELECT 1',
            'ALTER TABLE enrollments ADD COLUMN semester VARCHAR(20) NULL');
PREPARE stmt FROM @s;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @s = IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'enrollments' AND COLUMN_NAME = 'assigned_by'),
            'SELECT 1',
            'ALTER TABLE enrollments ADD COLUMN assigned_by INT NULL');
PREPARE stmt FROM @s;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @s = IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'enrollments' AND COLUMN_NAME = 'updated_at'),
            'SELECT 1',
            'ALTER TABLE enrollments ADD COLUMN updated_at DATETIME NULL');
PREPARE stmt FROM @s;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------------
-- 2. Back-fill LEGACY rows only (rows that still have no status).
--    The newest row per student (highest id) becomes the current
--    enrollment; older rows are kept as history ('Transferred').
--    Nothing is deleted.
-- ---------------------------------------------------------------------
UPDATE enrollments e
JOIN (SELECT student_id, MAX(id) AS keep_id FROM enrollments GROUP BY student_id) k
  ON k.student_id = e.student_id
SET e.enrollment_status = IF(e.id = k.keep_id, 'Enrolled', 'Transferred'),
    e.is_current        = IF(e.id = k.keep_id, 1, NULL)
WHERE e.enrollment_status IS NULL;

-- ---------------------------------------------------------------------
-- 3. Final column definitions + constraints / indexes
-- ---------------------------------------------------------------------
ALTER TABLE enrollments MODIFY COLUMN enrollment_status VARCHAR(20) NOT NULL DEFAULT 'Enrolled';

ALTER TABLE enrollments MODIFY COLUMN is_current TINYINT NULL DEFAULT 1;

-- One CURRENT program per student (enforced by the database).
SET @s = IF(EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'enrollments' AND INDEX_NAME = 'uq_enrollment_student_current'),
            'SELECT 1',
            'ALTER TABLE enrollments ADD UNIQUE KEY uq_enrollment_student_current (student_id, is_current)');
PREPARE stmt FROM @s;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Fast filtering by program + status (Student Enrollment Management page).
SET @s = IF(EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'enrollments' AND INDEX_NAME = 'idx_enrollment_course_status'),
            'SELECT 1',
            'ALTER TABLE enrollments ADD INDEX idx_enrollment_course_status (course_id, enrollment_status)');
PREPARE stmt FROM @s;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------------
-- 4. course_documents: prospectus / curriculum files, versioned
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS course_documents (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    course_id         INT NOT NULL,
    document_title    VARCHAR(255) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    stored_filename   VARCHAR(255) NOT NULL,
    file_path         VARCHAR(500) NOT NULL COMMENT 'relative to PROSPECTUS_FOLDER, never absolute',
    file_type         VARCHAR(10)  NOT NULL,
    file_size         INT UNSIGNED NOT NULL DEFAULT 0,
    version           INT UNSIGNED NOT NULL DEFAULT 1,
    academic_year     VARCHAR(20)  NULL,
    is_current        TINYINT NULL DEFAULT NULL COMMENT '1 = official current document, NULL = archived/draft',
    is_published      TINYINT NOT NULL DEFAULT 0,
    uploaded_by       INT NULL,
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME NULL,
    UNIQUE KEY uq_course_version (course_id, version),
    UNIQUE KEY uq_course_current (course_id, is_current),
    KEY idx_course_published (course_id, is_published),
    CONSTRAINT fk_course_documents_course FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE,
    CONSTRAINT fk_course_documents_user FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
