import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'kll_secret_super_key_change_in_production_2024')
    MYSQL_HOST = os.environ.get('MYSQL_HOST', 'localhost')
    MYSQL_USER = os.environ.get('MYSQL_USER', 'root')
    MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD', 'anime951827')
    MYSQL_DB = os.environ.get('MYSQL_DB', 'kll_portal_db')
    MYSQL_CURSORCLASS = 'DictCursor'
    UPLOAD_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static', 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB max limit

    # --- Session / CSRF hardening ---
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # --- Prospectus / curriculum documents ---
    # Stored OUTSIDE static/ so files are only reachable through the
    # authorised Flask routes (never by guessing a /static/... URL).
    PROSPECTUS_FOLDER = os.environ.get(
        'PROSPECTUS_FOLDER',
        os.path.join(os.path.abspath(os.path.dirname(__file__)), 'protected_uploads', 'prospectus')
    )
    PROSPECTUS_MAX_BYTES = int(os.environ.get('PROSPECTUS_MAX_BYTES', 10 * 1024 * 1024))  # 10 MB
    PROSPECTUS_ALLOWED_EXTENSIONS = {'pdf', 'docx', 'doc'}  # PDF is the primary format

    # Run migrations/enrollment_prospectus_upgrade.sql automatically on start-up.
    AUTO_MIGRATE = os.environ.get('KLL_AUTO_MIGRATE', '1') == '1'

    # Email Configuration (Gmail)
    MAIL_SERVER = 'smtp.gmail.com'
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USE_SSL = False
    MAIL_USERNAME = 'polytechniccollegepadregarcia@gmail.com'
    MAIL_PASSWORD = 'vurv lekx wzxf vsze'
    MAIL_DEFAULT_SENDER = ('PGPC Admissions', 'polytechniccollegepadregarcia@gmail.com')
    ADMIN_EMAIL = 'polytechniccollegepadregarcia@gmail.com'