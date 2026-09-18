-- CMS Upgrade Migration
-- Run this SQL to upgrade the news table for categories and publishing status

-- Add category column to news table
ALTER TABLE news ADD COLUMN category VARCHAR(50) DEFAULT 'General' AFTER author_name;

-- Add status column (draft, published, scheduled) replacing is_active
ALTER TABLE news ADD COLUMN status VARCHAR(20) DEFAULT 'published' AFTER is_active;

-- Add publish_date for scheduled publishing
ALTER TABLE news ADD COLUMN publish_date DATETIME NULL AFTER status;

-- Migrate existing data: is_active=1 -> published, is_active=0 -> draft
UPDATE news SET status = 'published' WHERE is_active = 1;
UPDATE news SET status = 'draft' WHERE is_active = 0;

-- Create media_library table for central media management
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
