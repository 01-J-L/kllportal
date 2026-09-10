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

    # Email Configuration (Gmail)
    MAIL_SERVER = 'smtp.gmail.com'
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USE_SSL = False
    MAIL_USERNAME = 'polytechniccollegepadregarcia@gmail.com'
    MAIL_PASSWORD = 'vurv lekx wzxf vsze'
    MAIL_DEFAULT_SENDER = ('PGPC Admissions', 'polytechniccollegepadregarcia@gmail.com')
    ADMIN_EMAIL = 'polytechniccollegepadregarcia@gmail.com'