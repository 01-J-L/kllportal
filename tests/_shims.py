"""Test doubles so the Flask app can run WITHOUT a MySQL server / mysqlclient / SMTP.

`flask_mysqldb.MySQL` is replaced by a thin SQLite adapter that mimics the DictCursor API
(`%s` placeholders, dict rows, lastrowid). It exists only for the automated tests; the
production app still talks to MySQL through Flask-MySQLdb. The SQL used by the new features
is deliberately engine-neutral so the same statements run on both.
"""
import re
import sqlite3
import sys
import types


class _IntegrityError(Exception):
    pass


def install():
    # --- MySQLdb ---
    mysqldb = types.ModuleType('MySQLdb')
    mysqldb.IntegrityError = _IntegrityError
    sys.modules['MySQLdb'] = mysqldb

    # --- flask_mail ---
    fm = types.ModuleType('flask_mail')

    class Mail:
        def __init__(self, app=None): self.sent = []
        def send(self, msg): self.sent.append(msg)

    class Message:
        def __init__(self, **kw): self.__dict__.update(kw)

    fm.Mail, fm.Message = Mail, Message
    sys.modules['flask_mail'] = fm

    # --- flask_mysqldb ---
    fx = types.ModuleType('flask_mysqldb')

    class Cursor:
        def __init__(self, conn): self._c = conn.cursor(); self.lastrowid = None
        @staticmethod
        def _translate(sql):
            sql = sql.replace('%s', '?')
            sql = re.sub(r'\bNOW\(\)', 'CURRENT_TIMESTAMP', sql, flags=re.I)
            sql = re.sub(r'\s+FOR UPDATE\b', '', sql, flags=re.I)
            return sql
        def execute(self, sql, args=None):
            try:
                self._c.execute(self._translate(sql), tuple(args or ()))
            except sqlite3.IntegrityError as e:
                raise _IntegrityError(str(e))
            self.lastrowid = self._c.lastrowid
            return self
        def fetchone(self):
            r = self._c.fetchone()
            return dict(r) if r is not None else None
        def fetchall(self): return [dict(r) for r in self._c.fetchall()]
        def close(self): pass

    class MySQL:
        _conn = None
        def __init__(self, app=None): pass
        @property
        def connection(self):
            if MySQL._conn is None:
                MySQL._conn = sqlite3.connect(':memory:', check_same_thread=False)
                MySQL._conn.row_factory = sqlite3.Row
                MySQL._conn.execute('PRAGMA foreign_keys = ON')
            return _Conn(MySQL._conn)
        @classmethod
        def reset(cls):
            if cls._conn is not None:
                cls._conn.close()
            cls._conn = None

    class _Conn:
        def __init__(self, c): self._c = c
        def cursor(self): return Cursor(self._c)
        def commit(self): self._c.commit()
        def rollback(self): self._c.rollback()

    fx.MySQL = MySQL
    sys.modules['flask_mysqldb'] = fx
    return MySQL


# Mirror of the schema produced by migrations/enrollment_prospectus_upgrade.sql (+ tables the pages read).
SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL, phone_number TEXT, role TEXT DEFAULT 'STUDENT', status TEXT DEFAULT 'Active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE courses (id INTEGER PRIMARY KEY AUTOINCREMENT, course_code TEXT NOT NULL, course_name TEXT NOT NULL,
    description TEXT, credits REAL DEFAULT 0, curriculum_description TEXT, objectives TEXT, outcomes TEXT,
    is_active INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE enrollments (id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    enrollment_status TEXT NOT NULL DEFAULT 'Enrolled', is_current INTEGER NULL DEFAULT 1,
    academic_year TEXT, semester TEXT, assigned_by INTEGER, updated_at TIMESTAMP,
    UNIQUE (student_id, is_current));
CREATE TABLE course_documents (id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    document_title TEXT NOT NULL, original_filename TEXT NOT NULL, stored_filename TEXT NOT NULL,
    file_path TEXT NOT NULL, file_type TEXT NOT NULL, file_size INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1, academic_year TEXT, is_current INTEGER NULL DEFAULT NULL,
    is_published INTEGER NOT NULL DEFAULT 0, uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP,
    UNIQUE (course_id, version), UNIQUE (course_id, is_current));
CREATE TABLE site_settings (setting_key TEXT PRIMARY KEY, setting_value TEXT);
CREATE TABLE emergency_advisories (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, message TEXT,
    advisory_type TEXT, is_active INTEGER DEFAULT 1, expires_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE clubs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, details TEXT, is_active INTEGER DEFAULT 1);
CREATE TABLE news (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, content TEXT, is_active INTEGER DEFAULT 1);
"""
