import os
import io
import re
import uuid
import hmac
import secrets
import zipfile
from datetime import datetime
from urllib.parse import urlparse
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for, session,
                   flash, jsonify, send_file, abort)
from markupsafe import Markup
from flask_mysqldb import MySQL
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from config import Config

try:  # mysqlclient (installed with Flask-MySQLdb)
    from MySQLdb import IntegrityError as DBIntegrityError
except ImportError:  # pragma: no cover - only if the driver is missing
    DBIntegrityError = ()

app = Flask(__name__)
app.config.from_object(Config)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['PROSPECTUS_FOLDER'], exist_ok=True)
mysql = MySQL(app)
mail = Mail(app)

# Create password_reset_tokens table if it doesn't exist
with app.app_context():
    cur = mysql.connection.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                token VARCHAR(64) NOT NULL UNIQUE,
                expires_at DATETIME NOT NULL,
                used TINYINT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        mysql.connection.commit()
    except Exception:
        pass
    cur.close()

# CMS Upgrade: auto-create tables and add news columns
with app.app_context():
    try:
        cur = mysql.connection.cursor()
        # Add columns to news table if they don't exist
        for col, definition in [
            ("category", "VARCHAR(50) DEFAULT 'General'"),
            ("status", "VARCHAR(20) DEFAULT 'published'"),
            ("publish_date", "DATETIME NULL")
        ]:
            try:
                cur.execute(f"ALTER TABLE news ADD COLUMN {col} {definition}")
            except Exception:
                pass  # Column already exists

        # Add category column to events table if missing
        try:
            cur.execute("ALTER TABLE events ADD COLUMN category VARCHAR(50) DEFAULT 'Campus Events'")
        except Exception:
            pass

        # Add category column to clubs table if missing
        try:
            cur.execute("ALTER TABLE clubs ADD COLUMN category VARCHAR(50) DEFAULT 'Student Organization'")
        except Exception:
            pass

        # Add category column to virtual_tours table if missing
        try:
            cur.execute("ALTER TABLE virtual_tours ADD COLUMN category VARCHAR(50) DEFAULT 'Facilities'")
        except Exception:
            pass

        # Add category column to student_handbooks table if missing
        try:
            cur.execute("ALTER TABLE student_handbooks ADD COLUMN category VARCHAR(50) DEFAULT 'General'")
        except Exception:
            pass

        # Add expires_at to emergency_advisories if missing
        try:
            cur.execute("ALTER TABLE emergency_advisories ADD COLUMN expires_at DATETIME NULL")
        except Exception:
            pass

        # Ensure description/content columns are TEXT (not VARCHAR) for Quill HTML content
        alter_to_text = [
            ("academic_calendar", "description"),
            ("courses", "description"),
            ("courses", "curriculum_description"),
            ("courses", "objectives"),
            ("courses", "outcomes"),
            ("clubs", "details"),
            ("news", "content"),
            ("events", "description"),
            ("virtual_tours", "description"),
            ("about_sections", "body"),
            ("student_handbooks", "description"),
        ]
        for table, column in alter_to_text:
            try:
                cur.execute(f"ALTER TABLE `{table}` MODIFY COLUMN `{column}` LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                print(f"[MIGRATION] Altered {table}.{column} to LONGTEXT")
            except Exception as e:
                print(f"[MIGRATION] {table}.{column}: {e}")
                mysql.connection.rollback()

        # Emergency Advisories table
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS emergency_advisories (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    message TEXT NOT NULL,
                    advisory_type VARCHAR(30) DEFAULT 'info',
                    is_active TINYINT DEFAULT 1,
                    expires_at DATETIME NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        except Exception:
            pass

        mysql.connection.commit()
        cur.close()
    except Exception:
        pass

def run_sql_migration(path):
    """Run an idempotent .sql migration file statement by statement.

    The file must follow the conventions documented at the top of
    migrations/enrollment_prospectus_upgrade.sql (';'-terminated statements,
    no DELIMITER blocks). Failures are logged loudly but never stop the app.
    """
    try:
        with open(path, encoding='utf-8') as fh:
            lines = [ln for ln in fh.read().splitlines() if not ln.strip().startswith('--')]
    except OSError as err:
        print(f"[MIGRATION] Cannot read {path}: {err}")
        return False
    statements = [s.strip() for s in '\n'.join(lines).split(';') if s.strip()]
    ok = True
    with app.app_context():
        cur = mysql.connection.cursor()
        for stmt in statements:
            try:
                cur.execute(stmt)
            except Exception as err:
                ok = False
                print(f"[MIGRATION] FAILED: {stmt[:90]!r} -> {err}")
                break
        try:
            mysql.connection.commit()
        except Exception:
            pass
        cur.close()
    print(f"[MIGRATION] {os.path.basename(path)}: {'OK' if ok else 'INCOMPLETE - run it manually and check the error above'}")
    return ok


if app.config.get('AUTO_MIGRATE'):
    run_sql_migration(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   'migrations', 'enrollment_prospectus_upgrade.sql'))

def teacher_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') not in ['TEACHER', 'ADMIN', 'SUPER_ADMIN']:
            flash('Faculty / Teacher access required.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/teacher/dashboard')
@teacher_required
def teacher_dashboard():
    cur = mysql.connection.cursor()

    # Get teacher name and split into last name, first name format
    full_name = session.get('user_name', '')
    # Remove 'Prof.' prefix if present
    clean_name = full_name.replace('Prof.', '').strip()
    name_parts = clean_name.split()
    if len(name_parts) >= 2:
        # Last word is last name, everything else is first name(s)
        teacher_last_name = name_parts[-1]
        teacher_first_name = ' '.join(name_parts[:-1])
    else:
        teacher_last_name = clean_name
        teacher_first_name = ''

    # 1. Active Courses
    cur.execute("SELECT * FROM courses WHERE is_active = 1 ORDER BY course_name ASC")
    courses = cur.fetchall()

    # 2. Upcoming Academic Events
    cur.execute("SELECT * FROM academic_calendar WHERE is_active = 1 ORDER BY start_date ASC LIMIT 5")
    events = cur.fetchall()

    # 3. Total active students count
    cur.execute("SELECT COUNT(*) as student_count FROM users WHERE role = 'STUDENT' AND status = 'Active'")
    student_count = cur.fetchone()['student_count']

    # 4. Total enrollments count
    cur.execute("SELECT COUNT(*) as enrollment_count FROM enrollments")
    enrollment_count = cur.fetchone()['enrollment_count']

    cur.close()

    return render_template(
        'portal/teacher_dashboard.html',
        courses=courses,
        events=events,
        student_count=student_count,
        enrollment_count=enrollment_count,
        teacher_last_name=teacher_last_name,
        teacher_first_name=teacher_first_name
    )

def send_email(subject, recipient, html_body):
    try:
        msg = Message(subject=subject, recipients=[recipient], html=html_body)
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Email send failed: {e}")
        return False

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'webp', 'mp4', 'webm'}

import re

def extract_youtube_id(url_or_id):
    if not url_or_id:
        return ''
    url_or_id = url_or_id.strip()
    match = re.search(r'(?:v=|\/|youtu\.be\/)([0-9A-Za-z_-]{11})', url_or_id)
    if match:
        return match.group(1)
    return url_or_id

def get_site_setting(key, default=None):
    cur = mysql.connection.cursor()
    cur.execute("SELECT setting_value FROM site_settings WHERE setting_key = %s", (key,))
    row = cur.fetchone()
    cur.close()
    return row['setting_value'] if row else default

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_uploaded_file(file_obj):
    if file_obj and file_obj.filename != '' and allowed_file(file_obj.filename):
        fname = secure_filename(file_obj.filename)
        file_obj.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
        return fname
    return None

# --- AUTH DECORATORS ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') not in ['ADMIN', 'SUPER_ADMIN']:
            flash('Admin access required.', 'danger')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def clean_map_url(raw_input):
    if not raw_input:
        return 'https://maps.google.com/maps?q=Kolehiyo%20ng%20Lungsod%20ng%20Lipa&t=&z=15&ie=UTF8&iwloc=&output=embed'
    raw_input = raw_input.strip()
    
    # 1. If user pasted the whole <iframe src="..."> code, extract src
    iframe_match = re.search(r'src=["\']([^"\']+)["\']', raw_input)
    if iframe_match:
        return iframe_match.group(1)
        
    # 2. If it's already an embed link
    if 'output=embed' in raw_input:
        return raw_input
        
    # 3. If it's a standard google search/share link or address string, wrap into embed format
    if not raw_input.startswith('http'):
        return f"https://maps.google.com/maps?q={raw_input}&t=&z=15&ie=UTF8&iwloc=&output=embed"
        
    return raw_input

@app.context_processor
def inject_site_settings():
    return {
        'top_phone': get_site_setting('top_phone'),
        'top_email': get_site_setting('top_email'),
        'facebook_url': get_site_setting('facebook_url', '#'),
        'twitter_url': get_site_setting('twitter_url', '#'),
        'linkedin_url': get_site_setting('linkedin_url', '#'),
        'instagram_url': get_site_setting('instagram_url', '#'),
        'footer_address': get_site_setting('footer_address', 'Marawoy, Lipa City, Batangas, Philippines\nKolehiyo ng Lungsod ng Lipa Main Campus'),
        'footer_phone': get_site_setting('footer_phone'),
        'footer_email': get_site_setting('footer_email'),
        'footer_map_url': clean_map_url(get_site_setting('footer_map_url'))
    }

# ==================== CSRF PROTECTION ==================== #
# Session-bound token, checked on every state-changing request. Templates add
# it with {{ csrf_input() }} inside each <form method="POST">; AJAX callers
# may send it in the X-CSRFToken header. No extra dependency required.
def _get_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


@app.context_processor
def inject_csrf():
    return {
        'csrf_token': _get_csrf_token,
        'csrf_input': lambda: Markup(
            '<input type="hidden" name="csrf_token" value="%s">' % _get_csrf_token()),
    }


def _safe_back(default_endpoint='home'):
    """Redirect target: the referring page if it is on this host, else a default."""
    ref = request.referrer
    if ref and urlparse(ref).netloc == request.host:
        return ref
    return url_for(default_endpoint)


@app.before_request
def csrf_protect():
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return None
    sent = request.form.get('csrf_token') or request.headers.get('X-CSRFToken') or ''
    expected = session.get('_csrf_token', '')
    if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
        app.logger.warning('CSRF check failed for %s %s', request.method, request.path)
        flash('Your session expired or the request could not be verified. Please try again.', 'danger')
        return redirect(_safe_back(), code=303)
    return None


@app.errorhandler(RequestEntityTooLarge)
def handle_too_large(_err):
    limit_mb = app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)
    flash(f'The uploaded file is too large (the server limit is {limit_mb} MB).', 'danger')
    return redirect(_safe_back(), code=303)


# ==================== TEMPLATE FILTERS ==================== #
@app.template_filter('fmt_date')
def fmt_date(value):
    """'November 14, 2026' from a datetime (MySQL) or ISO string."""
    if not value:
        return '—'
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value[:19])
        except ValueError:
            return value
    return f"{value:%B} {value.day}, {value.year}"


@app.template_filter('filesize')
def filesize(num):
    num = int(num or 0)
    if num >= 1024 * 1024:
        return f"{num / (1024 * 1024):.1f} MB"
    return f"{max(1, round(num / 1024))} KB" if num else '0 KB'


# ==================== PUBLIC HOME ROUTE ==================== #
@app.route('/')
def home():
    cur = mysql.connection.cursor()
    
    # 1. Fetch active about sections (Vision, Mission, etc.)
    cur.execute("SELECT * FROM about_sections WHERE is_active = 1 ORDER BY id ASC")
    about_sections = cur.fetchall()

    # 2. Fetch first 3 active courses
    cur.execute("SELECT * FROM courses WHERE is_active = 1 ORDER BY id ASC LIMIT 3")
    courses = cur.fetchall()

    # 3. Fetch first 3 latest active news items
    cur.execute("SELECT * FROM news WHERE status = 'published' AND (publish_date IS NULL OR publish_date <= NOW()) ORDER BY id DESC LIMIT 3")
    news_items = cur.fetchall()

    cur.close()

    # Dynamic Hero Settings
    hero_image = get_site_setting('hero_banner_image', '')
    hero_title = get_site_setting('hero_title', 'KOLEHIYO NG LUNGSOD NG LIPA')
    hero_subtitle = get_site_setting('hero_subtitle', 'Your Gateway to Academic Excellence · Quality Education for All')

    return render_template(
        'portal/index.html',
        about_sections=about_sections,
        courses=courses,
        news=news_items,
        hero_image=hero_image,
        hero_title=hero_title,
        hero_subtitle=hero_subtitle
    )

@app.route('/about')
def about():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM about_sections WHERE is_active = 1 ORDER BY id ASC")
    about_sections = cur.fetchall()
    cur.close()

    hero_image = get_site_setting('hero_banner_image', '')
    org_chart_image = get_site_setting('org_chart_image', '')
    admission_file = get_site_setting('admission_form_file', '')

    return render_template(
        'portal/about.html',
        about_sections=about_sections,
        hero_image=hero_image,
        org_chart_image=org_chart_image,
        admission_file=admission_file
    )

@app.route('/courses')
def courses():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM courses WHERE is_active = 1 ORDER BY id ASC")
    courses_list = cur.fetchall()
    cur.close()
    hero_image = get_site_setting('hero_banner_image', '')
    return render_template('portal/courses.html', courses=courses_list, hero_image=hero_image)

@app.route('/academic-calendar')
def academic_calendar():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM academic_calendar WHERE is_active = 1 ORDER BY start_date ASC")
    events = cur.fetchall()
    cur.close()
    hero_image = get_site_setting('hero_banner_image', '')
    return render_template('portal/calendar.html', events=events, hero_image=hero_image)


@app.route('/events-gallery')
def events_gallery():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM events WHERE is_active = 1 ORDER BY event_date DESC, id DESC")
    all_events = cur.fetchall()
    cur.execute("SELECT DISTINCT category FROM events WHERE is_active = 1 AND category IS NOT NULL ORDER BY category")
    categories = [row['category'] for row in cur.fetchall()]
    cur.close()
    hero_image = get_site_setting('hero_banner_image', '')
    return render_template('portal/events_gallery.html', events=all_events, categories=categories, hero_image=hero_image)

@app.route('/news')
def news():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM news WHERE status = 'published' AND (publish_date IS NULL OR publish_date <= NOW()) ORDER BY id DESC")
    all_news = cur.fetchall()
    cur.close()
    hero_image = get_site_setting('hero_banner_image', '')
    return render_template('portal/news.html', news=all_news, hero_image=hero_image)

@app.route('/clubs')
def clubs():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM clubs WHERE is_active = 1 ORDER BY id DESC")
    all_clubs = cur.fetchall()
    cur.execute("SELECT DISTINCT category FROM clubs WHERE is_active = 1 AND category IS NOT NULL ORDER BY category")
    categories = [row['category'] for row in cur.fetchall()]
    cur.close()
    hero_image = get_site_setting('hero_banner_image', '')
    return render_template('portal/clubs.html', clubs=all_clubs, categories=categories, hero_image=hero_image)

@app.route('/virtual-tour')
def virtual_tour():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM virtual_tours WHERE is_active = 1 ORDER BY id DESC")
    tours = cur.fetchall()
    cur.execute("SELECT DISTINCT category FROM virtual_tours WHERE is_active = 1 AND category IS NOT NULL ORDER BY category")
    categories = [row['category'] for row in cur.fetchall()]
    cur.close()
    hero_image = get_site_setting('hero_banner_image', '')
    return render_template('portal/virtual_tour.html', tours=tours, categories=categories, hero_image=hero_image)

# ==================== PUBLIC STUDENT HANDBOOK ROUTE ==================== #
@app.route('/student-handbook')
def handbook():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM student_handbooks WHERE is_active = 1 ORDER BY id ASC")
    handbooks = cur.fetchall()
    cur.execute("SELECT DISTINCT category FROM student_handbooks WHERE is_active = 1 AND category IS NOT NULL ORDER BY category")
    categories = [row['category'] for row in cur.fetchall()]
    cur.close()
    hero_image = get_site_setting('hero_banner_image', '')
    return render_template('portal/handbook.html', handbooks=handbooks, categories=categories, hero_image=hero_image)

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    hero_image = get_site_setting('hero_banner_image', '')
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        subject = request.form.get('subject')
        msg = request.form.get('message')

        cur = mysql.connection.cursor()
        cur.execute("INSERT INTO inquiries (name, email, phone, subject, message) VALUES (%s, %s, %s, %s, %s)",
                    (name, email, phone, subject, msg))
        mysql.connection.commit()
        cur.close()

        # Send email notification to admin
        admin_email = app.config.get('ADMIN_EMAIL')
        email_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background-color: #1e60d5; color: white; padding: 20px; text-align: center;">
                <h2 style="margin: 0;">New Contact Inquiry</h2>
            </div>
            <div style="padding: 20px; border: 1px solid #ddd;">
                <p><strong>Name:</strong> {name}</p>
                <p><strong>Email:</strong> {email}</p>
                <p><strong>Phone:</strong> {phone or 'N/A'}</p>
                <p><strong>Subject:</strong> {subject or 'N/A'}</p>
                <hr>
                <p><strong>Message:</strong></p>
                <p style="white-space: pre-wrap;">{msg}</p>
            </div>
            <div style="padding: 10px; text-align: center; font-size: 12px; color: #888;">
                This message was sent via the KLL Portal contact form.
            </div>
        </div>
        """
        send_email(f"New Inquiry: {subject or 'No Subject'}", admin_email, email_html)

        flash('Message successfully sent!', 'success')
        return redirect(url_for('contact'))
    return render_template('portal/contact.html', hero_image=hero_image)

# --- AUTH & PROFILE ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    # If already logged in, route to appropriate area
    if 'user_id' in session:
        if session.get('role') in ['ADMIN', 'SUPER_ADMIN']:
            return redirect(url_for('admin_dashboard'))
        elif session.get('role') == 'TEACHER':
            return redirect(url_for('teacher_dashboard'))
        return redirect(url_for('profile'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()

        if user and (check_password_hash(user['password_hash'], password) or user['password_hash'] == password):
            if user['status'] != 'Active':
                flash('Your account has been deactivated or suspended. Please contact the administrator.', 'danger')
                return render_template('portal/login.html')

            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['email'] = user['email']
            session['phone_number'] = user['phone_number']
            session['role'] = user['role']

            flash(f"Welcome back, {user['name']}!", 'success')

            # --- ROLE BASED ROUTING ---
            if user['role'] in ['ADMIN', 'SUPER_ADMIN']:
                return redirect(url_for('admin_dashboard'))
            elif user['role'] == 'TEACHER':
                return redirect(url_for('teacher_dashboard'))
            else:
                return redirect(url_for('profile'))

        flash('Invalid email or password credentials.', 'danger')

    return render_template('portal/login.html')

# ==================== STUDENT REGISTRATION ==================== #
@app.route('/register', methods=['GET', 'POST'])
def register():
    # If user is already logged in, redirect them to their profile
    if 'user_id' in session:
        return redirect(url_for('profile'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone_number', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        # Basic validations
        if not name or not email or not password:
            flash('Please fill in all required fields.', 'warning')
            return render_template('portal/register.html')

        if password != confirm_password:
            flash('Passwords do not match. Please try again.', 'danger')
            return render_template('portal/register.html')

        cur = mysql.connection.cursor()
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        existing_user = cur.fetchone()

        if existing_user:
            cur.close()
            flash('Email is already registered. Please sign in instead.', 'danger')
            return redirect(url_for('login'))

        # Secure password hashing
        pw_hash = generate_password_hash(password)

        # Insert new student record
        cur.execute("""
            INSERT INTO users (name, email, password_hash, phone_number, role, status)
            VALUES (%s, %s, %s, %s, 'STUDENT', 'Active')
        """, (name, email, pw_hash, phone))
        mysql.connection.commit()

        # Retrieve new user id to log in automatically
        cur.execute("SELECT id, name, email, phone_number, role FROM users WHERE email = %s", (email,))
        new_user = cur.fetchone()
        cur.close()

        # Start session
        session['user_id'] = new_user['id']
        session['user_name'] = new_user['name']
        session['email'] = new_user['email']
        session['phone_number'] = new_user['phone_number']
        session['role'] = new_user['role']

        flash('Registration successful! Welcome to KLL Portal.', 'success')
        return redirect(url_for('profile'))

    return render_template('portal/register.html')

@app.route('/profile')
@login_required
def profile():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM users WHERE id = %s", (session['user_id'],))
    user = cur.fetchone()

    if not user:
        cur.close()
        session.clear()
        flash('Your account could not be found. Please log in again.', 'warning')
        return redirect(url_for('login'))

    academic = None          # the student's CURRENT program (own record only)
    prospectus = None        # published curriculum of that program
    recommendations = []
    is_student = user['role'] == 'STUDENT'

    if is_student:
        # Always keyed on the authenticated session user - never on a request parameter.
        academic = get_student_enrollment(cur, user['id'])

        if academic:
            prospectus = get_student_prospectus(cur, user['id'])

            # Recommendations are only generated for enrolled students (unchanged behaviour)
            cur.execute("SELECT id, name, details FROM clubs WHERE is_active = 1 LIMIT 2")
            for club in cur.fetchall():
                recommendations.append({
                    'icon': 'fa-users',
                    'color': '#10b981',
                    'title': club['name'],
                    'desc': (club['details'][:80] + '...') if club['details'] and len(club['details']) > 80 else (club['details'] or 'Join this student organization.'),
                    'link': url_for('clubs'),
                    'badge': 'Club'
                })

            cur.execute("SELECT id, title, content FROM news WHERE is_active = 1 ORDER BY id DESC LIMIT 2")
            for article in cur.fetchall():
                recommendations.append({
                    'icon': 'fa-newspaper',
                    'color': '#f59e0b',
                    'title': article['title'],
                    'desc': (article['content'][:80] + '...') if article['content'] and len(article['content']) > 80 else (article['content'] or 'Read announcement.'),
                    'link': url_for('news'),
                    'badge': 'Announcement'
                })

    cur.close()

    return render_template(
        'portal/profile.html',
        user=user,
        is_student=is_student,
        academic=academic,
        prospectus=prospectus,
        recommendations=recommendations
    )

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.', 'info')
    return redirect(url_for('home'))

# --- FORGOT & RESET PASSWORD ---
@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        cur = mysql.connection.cursor()
        cur.execute("SELECT id, name FROM users WHERE email = %s", (email,))
        user = cur.fetchone()

        if user:
            import secrets
            token = secrets.token_hex(32)
            from datetime import datetime, timedelta
            expires_at = datetime.now() + timedelta(hours=1)

            # Delete any old tokens for this user
            cur.execute("DELETE FROM password_reset_tokens WHERE user_id = %s", (user['id'],))
            cur.execute(
                "INSERT INTO password_reset_tokens (user_id, token, expires_at) VALUES (%s, %s, %s)",
                (user['id'], token, expires_at)
            )
            mysql.connection.commit()

            reset_url = url_for('reset_password', token=token, _external=True)
            email_html = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <div style="background-color: #1e60d5; color: white; padding: 20px; text-align: center;">
                    <h2 style="margin: 0;">Password Reset Request</h2>
                </div>
                <div style="padding: 20px; border: 1px solid #ddd;">
                    <p>Hello <strong>{user['name']}</strong>,</p>
                    <p>We received a request to reset your password for the KLL Portal.</p>
                    <p>Click the button below to set a new password. This link expires in <strong>1 hour</strong>.</p>
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{reset_url}" style="background-color: #1e60d5; color: white; padding: 14px 32px; text-decoration: none; border-radius: 6px; font-weight: bold;">Reset Password</a>
                    </div>
                    <p style="font-size: 13px; color: #888;">If you did not request this, you can safely ignore this email.</p>
                </div>
            </div>
            """
            send_email("KLL Portal - Password Reset Request", email, email_html)

        # Always show the same message to prevent email enumeration
        flash('If that email is registered, a reset link has been sent.', 'info')
        return redirect(url_for('login'))
    return render_template('portal/forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    cur = mysql.connection.cursor()
    from datetime import datetime
    cur.execute("""
        SELECT prt.id, prt.user_id, prt.expires_at
        FROM password_reset_tokens prt
        WHERE prt.token = %s AND prt.used = 0 AND prt.expires_at > %s
    """, (token, datetime.now()))
    token_row = cur.fetchone()

    if not token_row:
        cur.close()
        flash('Invalid or expired reset link. Please request a new one.', 'danger')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'warning')
            return render_template('portal/reset_password.html', token=token)

        if password != confirm:
            flash('Passwords do not match.', 'danger')
            return render_template('portal/reset_password.html', token=token)

        pw_hash = generate_password_hash(password)
        cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (pw_hash, token_row['user_id']))
        cur.execute("UPDATE password_reset_tokens SET used = 1 WHERE id = %s", (token_row['id'],))
        mysql.connection.commit()
        cur.close()

        flash('Password updated successfully! You can now log in.', 'success')
        return redirect(url_for('login'))

    cur.close()
    return render_template('portal/reset_password.html', token=token)

# ==================== ADMIN PORTAL CRUD ==================== #

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM users WHERE email = %s AND role IN ('ADMIN', 'SUPER_ADMIN')", (email,))
        admin = cur.fetchone()
        cur.close()

        if admin and (check_password_hash(admin['password_hash'], password) or admin['password_hash'] == password):
            session['user_id'] = admin['id']
            session['user_name'] = admin['name']
            session['role'] = admin['role']
            return redirect(url_for('admin_dashboard'))
        flash('Invalid administrative credentials.', 'danger')
    return render_template('admin/login.html')

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    cur = mysql.connection.cursor()

    # 1. Total Students & Active Students
    cur.execute("SELECT COUNT(*) AS total_students FROM users WHERE role = 'STUDENT'")
    total_students = cur.fetchone()['total_students']

    cur.execute("SELECT COUNT(*) AS active_students FROM users WHERE role = 'STUDENT' AND status = 'Active'")
    active_students = cur.fetchone()['active_students']

    # 2. Total Faculty / Teachers & Admins
    cur.execute("SELECT COUNT(*) AS total_teachers FROM users WHERE role = 'TEACHER'")
    total_teachers = cur.fetchone()['total_teachers']

    # 3. Total Active Courses
    cur.execute("SELECT COUNT(*) AS total_courses FROM courses WHERE is_active = 1")
    total_courses = cur.fetchone()['total_courses']

    # 4. Total Events
    cur.execute("SELECT COUNT(*) AS total_events FROM events WHERE is_active = 1")
    total_events = cur.fetchone()['total_events']

    # 5. Total Clubs
    cur.execute("SELECT COUNT(*) AS total_clubs FROM clubs WHERE is_active = 1")
    total_clubs = cur.fetchone()['total_clubs']

    # 6. Student registrations per month (line chart - last 12 months)
    cur.execute("""
        SELECT YEAR(created_at) AS yr, MONTH(created_at) AS mo, COUNT(*) AS reg_count
        FROM users
        WHERE role = 'STUDENT'
          AND created_at >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
        GROUP BY yr, mo
        ORDER BY yr ASC, mo ASC
    """)
    monthly_data = cur.fetchall()

    # Build full 12-month series using calendar month alignment
    from datetime import datetime
    import calendar
    now = datetime.now()
    month_series = []
    for i in range(11, -1, -1):
        year = now.year
        month = now.month - i
        while month <= 0:
            month += 12
            year -= 1
        key = f"{year}-{month:02d}"
        label = calendar.month_abbr[month] + f" {year}"
        month_series.append({'key': key, 'label': label, 'count': 0})

    reg_map = {}
    for r in monthly_data:
        key = f"{r['yr']}-{r['mo']:02d}"
        reg_map[key] = r['reg_count']

    for m in month_series:
        if m['key'] in reg_map:
            m['count'] = reg_map[m['key']]

    monthly_labels = [m['label'] for m in month_series]
    monthly_counts = [int(m['count']) for m in month_series]

    # 8. Status distribution (pie/doughnut)
    cur.execute("SELECT status, COUNT(*) as cnt FROM users WHERE role='STUDENT' GROUP BY status")
    status_data = cur.fetchall()
    status_labels = [r['status'] for r in status_data]
    status_counts = [r['cnt'] for r in status_data]

    # 9. Role distribution (doughnut) - exclude SUPER_ADMIN
    cur.execute("SELECT role, COUNT(*) as cnt FROM users WHERE role != 'SUPER_ADMIN' GROUP BY role")
    role_data = cur.fetchall()
    role_labels = [r['role'].replace('_', ' ') for r in role_data]
    role_counts = [r['cnt'] for r in role_data]

    cur.close()

    return render_template(
        'admin/dashboard.html',
        total_students=total_students,
        active_students=active_students,
        total_teachers=total_teachers,
        total_courses=total_courses,
        total_events=total_events,
        total_clubs=total_clubs,
        monthly_labels=monthly_labels,
        monthly_counts=monthly_counts,
        status_labels=status_labels,
        status_counts=status_counts,
        role_labels=role_labels,
        role_counts=role_counts
    )

# ==================== ADMIN STUDENT HANDBOOK CRUD ==================== #
@app.route('/admin/handbook')
@admin_required
def admin_handbook():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM student_handbooks ORDER BY id DESC")
    handbooks = cur.fetchall()
    cur.close()
    return render_template('admin/handbook.html', handbooks=handbooks)


@app.route('/admin/handbook/create', methods=['POST'])
@admin_required
def admin_create_handbook():
    title = request.form.get('title')
    description = request.form.get('description')
    category = request.form.get('category', 'General')
    file_obj = request.files.get('pdf_file')
    
    if not file_obj or file_obj.filename == '':
        flash('Please select a PDF document to upload.', 'warning')
        return redirect(url_for('admin_handbook'))

    # Calculate readable file size
    file_obj.seek(0, os.SEEK_END)
    size_in_bytes = file_obj.tell()
    file_obj.seek(0)
    size_mb = round(size_in_bytes / (1024 * 1024), 1)
    file_size_label = f"PDF &bull; {size_mb} MB" if size_mb >= 0.1 else f"PDF &bull; {round(size_in_bytes / 1024)} KB"

    uploaded_filename = save_uploaded_file(file_obj)
    if uploaded_filename:
        cur = mysql.connection.cursor()
        cur.execute("""
            INSERT INTO student_handbooks (title, description, file_url, file_size, category, is_active)
            VALUES (%s, %s, %s, %s, %s, 1)
        """, (title, description, uploaded_filename, file_size_label, category))
        mysql.connection.commit()
        cur.close()
        flash('Handbook document published successfully.', 'success')
    else:
        flash('Invalid file format. Please upload a valid PDF.', 'danger')

    return redirect(url_for('admin_handbook'))


@app.route('/admin/handbook/edit/<int:id>', methods=['POST'])
@admin_required
def admin_edit_handbook(id):
    title = request.form.get('title')
    description = request.form.get('description')
    category = request.form.get('category', 'General')
    is_active = 1 if request.form.get('is_active') in ['1', 'on', True] else 0
    file_obj = request.files.get('pdf_file')

    cur = mysql.connection.cursor()
    if file_obj and file_obj.filename != '':
        file_obj.seek(0, os.SEEK_END)
        size_in_bytes = file_obj.tell()
        file_obj.seek(0)
        size_mb = round(size_in_bytes / (1024 * 1024), 1)
        file_size_label = f"PDF &bull; {size_mb} MB" if size_mb >= 0.1 else f"PDF &bull; {round(size_in_bytes / 1024)} KB"

        uploaded_filename = save_uploaded_file(file_obj)
        cur.execute("""
            UPDATE student_handbooks 
            SET title=%s, description=%s, file_url=%s, file_size=%s, category=%s, is_active=%s 
            WHERE id=%s
        """, (title, description, uploaded_filename, file_size_label, category, is_active, id))
    else:
        cur.execute("""
            UPDATE student_handbooks 
            SET title=%s, description=%s, category=%s, is_active=%s 
            WHERE id=%s
        """, (title, description, category, is_active, id))

    mysql.connection.commit()
    cur.close()
    flash('Handbook document updated successfully.', 'success')
    return redirect(url_for('admin_handbook'))


@app.route('/admin/handbook/delete/<int:id>', methods=['POST'])
@admin_required
def admin_delete_handbook(id):
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM student_handbooks WHERE id = %s", (id,))
    mysql.connection.commit()
    cur.close()
    flash('Handbook document removed.', 'info')
    return redirect(url_for('admin_handbook'))

# ==================== EVENTS ADMIN CRUD ==================== #

@app.route('/admin/events')
@admin_required
def admin_events():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM events ORDER BY id DESC")
    events_list = cur.fetchall()
    cur.close()
    return render_template('admin/events.html', events=events_list)


@app.route('/admin/events/create', methods=['POST'])
@admin_required
def admin_create_event():
    title = request.form.get('title')
    description = request.form.get('description')
    event_date = request.form.get('event_date')
    link_url = request.form.get('link_url')
    category = request.form.get('category', 'Campus Events')
    img = save_uploaded_file(request.files.get('image'))

    cur = mysql.connection.cursor()
    cur.execute("""
        INSERT INTO events (title, description, event_date, image_url, link_url, category, is_active) 
        VALUES (%s, %s, %s, %s, %s, %s, 1)
    """, (title, description, event_date, img, link_url, category))
    mysql.connection.commit()
    cur.close()
    flash('Event created successfully.', 'success')
    return redirect(url_for('admin_events'))


@app.route('/admin/events/edit/<int:id>', methods=['POST'])
@admin_required
def admin_edit_event(id):
    title = request.form.get('title')
    description = request.form.get('description')
    event_date = request.form.get('event_date')
    link_url = request.form.get('link_url')
    category = request.form.get('category', 'Campus Events')
    is_active = 1 if request.form.get('is_active') in ['1', 'on', True] else 0
    img = save_uploaded_file(request.files.get('image'))

    cur = mysql.connection.cursor()
    if img:
        cur.execute("""
            UPDATE events 
            SET title=%s, description=%s, event_date=%s, image_url=%s, link_url=%s, category=%s, is_active=%s 
            WHERE id=%s
        """, (title, description, event_date, img, link_url, category, is_active, id))
    else:
        cur.execute("""
            UPDATE events 
            SET title=%s, description=%s, event_date=%s, link_url=%s, category=%s, is_active=%s 
            WHERE id=%s
        """, (title, description, event_date, link_url, category, is_active, id))
    mysql.connection.commit()
    cur.close()
    flash('Event updated successfully.', 'success')
    return redirect(url_for('admin_events'))


@app.route('/admin/events/delete/<int:id>', methods=['POST'])
@admin_required
def admin_delete_event(id):
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM events WHERE id=%s", (id,))
    mysql.connection.commit()
    cur.close()
    flash('Event removed.', 'info')
    return redirect(url_for('admin_events'))

# --- USERS CRUD ---
USER_ROLES = ('STUDENT', 'TEACHER', 'ADMIN', 'SUPER_ADMIN')
USER_STATUSES = ('Active', 'Suspended')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


@app.route('/admin/users')
@admin_required
def admin_users():
    cur = mysql.connection.cursor()
    # One row per user + that user's CURRENT program (if any), straight from the database.
    cur.execute("""
        SELECT u.*,
               e.course_id    AS program_id,
               e.enrolled_at  AS program_enrolled_at,
               c.course_code  AS program_code,
               c.course_name  AS program_name,
               c.is_active    AS program_active
        FROM users u
        LEFT JOIN enrollments e ON e.student_id = u.id AND e.is_current = 1
        LEFT JOIN courses c     ON c.id = e.course_id
        ORDER BY u.id DESC
    """)
    users = cur.fetchall()
    programs = get_active_programs(cur)
    cur.close()
    return render_template('admin/users.html', users=users, programs=programs)


@app.route('/admin/users/create', methods=['POST'])
@admin_required
def admin_create_user():
    name = (request.form.get('name') or '').strip()
    email = (request.form.get('email') or '').strip().lower()
    password = request.form.get('password') or ''
    phone = (request.form.get('phone_number') or '').strip()
    role = request.form.get('role') or 'STUDENT'
    status = request.form.get('status') or 'Active'

    error = None
    if not name or not email or not password:
        error = 'Full name, email address and password are required.'
    elif not EMAIL_RE.match(email):
        error = 'Please enter a valid email address.'
    elif len(password) < 6:
        error = 'Password must be at least 6 characters.'
    elif role not in USER_ROLES:
        error = 'Invalid role selected.'
    elif status not in USER_STATUSES:
        error = 'Invalid account status selected.'
    if error:
        flash(error, 'danger')
        return redirect(url_for('admin_users'))

    cur = mysql.connection.cursor()
    program_result = None
    try:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            flash('That email address is already registered.', 'danger')
            cur.close()
            return redirect(url_for('admin_users'))

        course_id = parse_program_id(request.form.get('course_id')) if 'course_id' in request.form else None
        if course_id is not None and role != 'STUDENT':
            raise ProgramAssignmentError('Only student accounts can be assigned an academic program.')

        cur.execute("INSERT INTO users (name, email, password_hash, phone_number, role, status) VALUES (%s, %s, %s, %s, %s, %s)",
                    (name, email, generate_password_hash(password), phone, role, status))
        new_id = cur.lastrowid
        if course_id is not None:
            program_result = set_student_program(cur, new_id, course_id, session['user_id'])
        mysql.connection.commit()
    except ProgramAssignmentError as err:
        mysql.connection.rollback()
        cur.close()
        flash(str(err), 'danger')
        return redirect(url_for('admin_users'))
    except DBIntegrityError:
        mysql.connection.rollback()
        cur.close()
        flash('That email address is already registered.', 'danger')
        return redirect(url_for('admin_users'))
    except Exception:
        mysql.connection.rollback()
        cur.close()
        app.logger.exception('admin_create_user failed')
        flash('Could not create the user because of a server error. Nothing was saved.', 'danger')
        return redirect(url_for('admin_users'))
    cur.close()

    flash('User registered.', 'success')
    if program_result:
        flash('Student academic program successfully updated.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/edit/<int:id>', methods=['POST'])
@admin_required
def admin_edit_user(id):
    name = (request.form.get('name') or '').strip()
    email = (request.form.get('email') or '').strip().lower()
    phone = (request.form.get('phone_number') or '').strip()
    role = request.form.get('role') or ''
    status = request.form.get('status') or ''
    password = request.form.get('password') or ''

    cur = mysql.connection.cursor()
    cur.execute("SELECT id, status FROM users WHERE id = %s", (id,))
    existing = cur.fetchone()
    if not existing:
        cur.close()
        flash('User not found.', 'danger')
        return redirect(url_for('admin_users'))

    error = None
    if not name or not email:
        error = 'Full name and email address are required.'
    elif not EMAIL_RE.match(email):
        error = 'Please enter a valid email address.'
    elif role not in USER_ROLES:
        error = 'Invalid role selected.'
    elif status not in USER_STATUSES and status != existing['status']:
        error = 'Invalid account status selected.'
    elif password and len(password) < 6:
        error = 'Password must be at least 6 characters.'
    if error:
        cur.close()
        flash(error, 'danger')
        return redirect(url_for('admin_users'))

    program_result = None
    try:
        cur.execute("SELECT id FROM users WHERE email = %s AND id <> %s", (email, id))
        if cur.fetchone():
            raise ProgramAssignmentError('That email address is already used by another account.')

        # Only touch the enrollment when the form actually carried the dropdown,
        # so an untouched / absent field never wipes an existing assignment.
        wants_program = 'course_id' in request.form
        course_id = parse_program_id(request.form.get('course_id')) if wants_program else None
        if wants_program and course_id is not None and role != 'STUDENT':
            raise ProgramAssignmentError('Only student accounts can be assigned an academic program.')

        if password:
            cur.execute("UPDATE users SET name=%s, email=%s, phone_number=%s, role=%s, status=%s, password_hash=%s WHERE id=%s",
                        (name, email, phone, role, status, generate_password_hash(password), id))
        else:
            cur.execute("UPDATE users SET name=%s, email=%s, phone_number=%s, role=%s, status=%s WHERE id=%s",
                        (name, email, phone, role, status, id))

        if wants_program and role == 'STUDENT':
            program_result = set_student_program(cur, id, course_id, session['user_id'])
        mysql.connection.commit()
    except ProgramAssignmentError as err:
        mysql.connection.rollback()
        cur.close()
        flash(str(err), 'danger')
        return redirect(url_for('admin_users'))
    except DBIntegrityError:
        mysql.connection.rollback()
        cur.close()
        flash('Could not save: that email address or enrollment already exists.', 'danger')
        return redirect(url_for('admin_users'))
    except Exception:
        mysql.connection.rollback()
        cur.close()
        app.logger.exception('admin_edit_user failed')
        flash('Could not update the user because of a server error. Nothing was saved.', 'danger')
        return redirect(url_for('admin_users'))
    cur.close()

    flash('User updated.', 'success')
    if program_result in ('assigned', 'changed', 'removed'):
        flash('Student academic program successfully updated.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/delete/<int:id>', methods=['POST'])
@admin_required
def admin_delete_user(id):
    if id == session['user_id']:
        flash('Cannot delete own active session.', 'danger')
        return redirect(url_for('admin_users'))
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM users WHERE id = %s", (id,))
    mysql.connection.commit()
    cur.close()
    flash('User record removed.', 'info')
    return redirect(url_for('admin_users'))

# --- COURSES CRUD ---
@app.route('/admin/courses')
@admin_required
def admin_courses():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM courses ORDER BY id ASC")
    courses = cur.fetchall()

    # All prospectus versions, newest first, grouped per program (one query, not N).
    cur.execute("""
        SELECT d.*, u.name AS uploader_name
        FROM course_documents d
        LEFT JOIN users u ON u.id = d.uploaded_by
        ORDER BY d.course_id ASC, d.version DESC
    """)
    documents = {}
    for d in cur.fetchall():
        documents.setdefault(d['course_id'], []).append(d)
    cur.close()
    return render_template('admin/courses.html', courses=courses, documents=documents)

@app.route('/admin/courses/create', methods=['POST'])
@admin_required
def admin_create_course():
    code = request.form.get('course_code')
    name = request.form.get('course_name')
    desc = request.form.get('description')
    credits = request.form.get('credits') or '0.00'
    curriculum = request.form.get('curriculum_description')
    objectives = request.form.get('objectives')
    outcomes = request.form.get('outcomes')

    cur = mysql.connection.cursor()
    cur.execute("""
        INSERT INTO courses (course_code, course_name, description, credits, curriculum_description, objectives, outcomes, is_active)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 1)
    """, (code, name, desc, credits, curriculum, objectives, outcomes))
    mysql.connection.commit()
    cur.close()
    flash('Course created successfully.', 'success')
    return redirect(url_for('admin_courses'))


@app.route('/admin/courses/edit/<int:id>', methods=['POST'])
@admin_required
def admin_edit_course(id):
    code = request.form.get('course_code')
    name = request.form.get('course_name')
    desc = request.form.get('description')
    credits = request.form.get('credits') or '0.00'
    curriculum = request.form.get('curriculum_description')
    objectives = request.form.get('objectives')
    outcomes = request.form.get('outcomes')
    is_active = 1 if request.form.get('is_active') in ['on', '1', 1, True] else 0

    cur = mysql.connection.cursor()
    cur.execute("""
        UPDATE courses 
        SET course_code=%s, course_name=%s, description=%s, credits=%s,
            curriculum_description=%s, objectives=%s, outcomes=%s, is_active=%s 
        WHERE id=%s
    """, (code, name, desc, credits, curriculum, objectives, outcomes, is_active, id))
    mysql.connection.commit()
    cur.close()
    flash('Course updated successfully.', 'success')
    return redirect(url_for('admin_courses'))


@app.route('/admin/courses/delete/<int:id>', methods=['POST'])
@admin_required
def admin_delete_course(id):
    cur = mysql.connection.cursor()
    # Deleting a program would silently cascade-delete every student's enrollment
    # history for it, so refuse and point the admin to the safe alternative.
    cur.execute("SELECT COUNT(*) AS n FROM enrollments WHERE course_id = %s", (id,))
    enrollment_count = cur.fetchone()['n']
    if enrollment_count:
        cur.close()
        flash(f'This program has {enrollment_count} enrollment record(s) and cannot be deleted. '
              'Reassign or remove its students, or mark the program Inactive instead.', 'danger')
        return redirect(url_for('admin_courses'))

    cur.execute("SELECT file_path FROM course_documents WHERE course_id = %s", (id,))
    stored_files = [r['file_path'] for r in cur.fetchall()]
    cur.execute("DELETE FROM courses WHERE id = %s", (id,))
    mysql.connection.commit()
    cur.close()
    for rel_path in stored_files:          # don't leave orphaned prospectus files behind
        _remove_prospectus_file(rel_path)
    flash('Course program removed.', 'info')
    return redirect(url_for('admin_courses'))

# --- CALENDAR CRUD ---
@app.route('/admin/calendar')
@admin_required
def admin_calendar():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM academic_calendar ORDER BY start_date DESC")
    events = cur.fetchall()
    cur.close()
    return render_template('admin/calendar.html', events=events)

@app.route('/admin/calendar/create', methods=['POST'])
@admin_required
def admin_create_calendar():
    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO academic_calendar (title, description, start_date, end_date) VALUES (%s, %s, %s, %s)",
                (request.form.get('title'), request.form.get('description'),
                 request.form.get('start_date'), request.form.get('end_date')))
    mysql.connection.commit()
    cur.close()
    flash('Event published to calendar.', 'success')
    return redirect(url_for('admin_calendar'))

@app.route('/admin/calendar/edit/<int:id>', methods=['POST'])
@admin_required
def admin_edit_calendar(id):
    is_active = 1 if request.form.get('is_active') == 'on' else 0
    cur = mysql.connection.cursor()
    cur.execute("""
        UPDATE academic_calendar SET title=%s, description=%s, start_date=%s, end_date=%s, is_active=%s WHERE id=%s
    """, (request.form.get('title'), request.form.get('description'),
          request.form.get('start_date'), request.form.get('end_date'), is_active, id))
    mysql.connection.commit()
    cur.close()
    flash('Calendar event updated.', 'success')
    return redirect(url_for('admin_calendar'))

@app.route('/admin/calendar/delete/<int:id>', methods=['POST'])
@admin_required
def admin_delete_calendar(id):
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM academic_calendar WHERE id = %s", (id,))
    mysql.connection.commit()
    cur.close()
    flash('Event removed.', 'info')
    return redirect(url_for('admin_calendar'))

# --- CLUBS CRUD ---
@app.route('/admin/clubs')
@admin_required
def admin_clubs():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM clubs ORDER BY id DESC")
    clubs = cur.fetchall()
    cur.close()
    return render_template('admin/clubs.html', clubs=clubs)

@app.route('/admin/clubs/create', methods=['POST'])
@admin_required
def admin_create_club():
    name = request.form.get('name')
    details = request.form.get('details')
    link_url = request.form.get('link_url')
    category = request.form.get('category', 'Student Organization')
    img_name = save_uploaded_file(request.files.get('image'))

    cur = mysql.connection.cursor()
    cur.execute("""
        INSERT INTO clubs (name, details, image_url, link_url, category, is_active) 
        VALUES (%s, %s, %s, %s, %s, 1)
    """, (name, details, img_name, link_url, category))
    mysql.connection.commit()
    cur.close()
    flash('Club registered successfully.', 'success')
    return redirect(url_for('admin_clubs'))

@app.route('/admin/clubs/edit/<int:id>', methods=['POST'])
@admin_required
def admin_edit_club(id):
    name = request.form.get('name')
    details = request.form.get('details')
    link_url = request.form.get('link_url')
    category = request.form.get('category', 'Student Organization')
    is_active = 1 if request.form.get('is_active') in ['1', 'on', True] else 0
    img_name = save_uploaded_file(request.files.get('image'))

    cur = mysql.connection.cursor()
    if img_name:
        cur.execute("""
            UPDATE clubs 
            SET name=%s, details=%s, image_url=%s, link_url=%s, category=%s, is_active=%s 
            WHERE id=%s
        """, (name, details, img_name, link_url, category, is_active, id))
    else:
        cur.execute("""
            UPDATE clubs 
            SET name=%s, details=%s, link_url=%s, category=%s, is_active=%s 
            WHERE id=%s
        """, (name, details, link_url, category, is_active, id))
    mysql.connection.commit()
    cur.close()
    flash('Club updated successfully.', 'success')
    return redirect(url_for('admin_clubs'))

@app.route('/admin/clubs/delete/<int:id>', methods=['POST'])
@admin_required
def admin_delete_club(id):
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM clubs WHERE id = %s", (id,))
    mysql.connection.commit()
    cur.close()
    flash('Club removed.', 'info')
    return redirect(url_for('admin_clubs'))

# --- NEWS CRUD ---
@app.route('/admin/news')
@admin_required
def admin_news():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM news ORDER BY id DESC")
    news_items = cur.fetchall()
    cur.close()
    return render_template('admin/news.html', news=news_items)


@app.route('/admin/news/create', methods=['POST'])
@admin_required
def admin_create_news():
    title = request.form.get('title')
    content = request.form.get('content')
    author = request.form.get('author_name') or session.get('user_name', 'Admin')
    link_url = request.form.get('link_url')
    category = request.form.get('category', 'General')
    status = request.form.get('status', 'published')
    publish_date = request.form.get('publish_date') or None
    img = save_uploaded_file(request.files.get('image'))

    cur = mysql.connection.cursor()
    cur.execute("""
        INSERT INTO news (title, content, image_url, author_name, link_url, category, status, publish_date, is_active) 
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (title, content, img, author, link_url, category, status, publish_date, 1 if status == 'published' else 0))
    mysql.connection.commit()
    cur.close()
    flash('News bulletin created successfully.', 'success')
    return redirect(url_for('admin_news'))


@app.route('/admin/news/edit/<int:id>', methods=['POST'])
@admin_required
def admin_edit_news(id):
    title = request.form.get('title')
    content = request.form.get('content')
    author = request.form.get('author_name') or session.get('user_name', 'Admin')
    link_url = request.form.get('link_url')
    category = request.form.get('category', 'General')
    status = request.form.get('status', 'published')
    publish_date = request.form.get('publish_date') or None
    is_active = 1 if status == 'published' else 0
    img = save_uploaded_file(request.files.get('image'))

    cur = mysql.connection.cursor()
    if img:
        cur.execute("""
            UPDATE news 
            SET title=%s, content=%s, author_name=%s, link_url=%s, image_url=%s, category=%s, status=%s, publish_date=%s, is_active=%s 
            WHERE id=%s
        """, (title, content, author, link_url, img, category, status, publish_date, is_active, id))
    else:
        cur.execute("""
            UPDATE news 
            SET title=%s, content=%s, author_name=%s, link_url=%s, category=%s, status=%s, publish_date=%s, is_active=%s 
            WHERE id=%s
        """, (title, content, author, link_url, category, status, publish_date, is_active, id))
    mysql.connection.commit()
    cur.close()
    flash('News bulletin updated successfully.', 'success')
    return redirect(url_for('admin_news'))


@app.route('/admin/news/delete/<int:id>', methods=['POST'])
@admin_required
def admin_delete_news(id):
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM news WHERE id = %s", (id,))
    mysql.connection.commit()
    cur.close()
    flash('News bulletin deleted.', 'info')
    return redirect(url_for('admin_news'))

# --- VIRTUAL TOUR CRUD ---
@app.route('/admin/virtual-tour')
@admin_required
def admin_virtual_tour():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM virtual_tours ORDER BY id DESC")
    tours = cur.fetchall()
    cur.close()
    return render_template('admin/virtual_tour.html', tours=tours)


@app.route('/admin/virtual-tour/create', methods=['POST'])
@admin_required
def admin_create_virtual_tour():
    title = request.form.get('title')
    description = request.form.get('description')
    raw_video_input = request.form.get('video_id')
    video_id = extract_youtube_id(raw_video_input)
    uploaded_file = save_uploaded_file(request.files.get('tour_file'))
    category = request.form.get('category', 'Facilities')

    # Determine tour_type
    tour_type = 'FILE' if uploaded_file else ('EMBED' if ('http://' in raw_video_input or 'https://' in raw_video_input) and not video_id else 'YOUTUBE')

    cur = mysql.connection.cursor()
    cur.execute("""
        INSERT INTO virtual_tours (title, description, video_id, file_url, tour_type, category, is_active)
        VALUES (%s, %s, %s, %s, %s, %s, 1)
    """, (title, description, video_id if video_id else raw_video_input, uploaded_file, tour_type, category))
    mysql.connection.commit()
    cur.close()
    flash('Virtual tour entry saved successfully.', 'success')
    return redirect(url_for('admin_virtual_tour'))


@app.route('/admin/virtual-tour/edit/<int:id>', methods=['POST'])
@admin_required
def admin_edit_virtual_tour(id):
    title = request.form.get('title')
    description = request.form.get('description')
    raw_video_input = request.form.get('video_id')
    video_id = extract_youtube_id(raw_video_input)
    category = request.form.get('category', 'Facilities')
    is_active = 1 if request.form.get('is_active') in ['1', 'on', True] else 0
    uploaded_file = save_uploaded_file(request.files.get('tour_file'))

    cur = mysql.connection.cursor()
    if uploaded_file:
        cur.execute("""
            UPDATE virtual_tours 
            SET title=%s, description=%s, file_url=%s, tour_type='FILE', category=%s, is_active=%s 
            WHERE id=%s
        """, (title, description, uploaded_file, category, is_active, id))
    else:
        # If text/link input provided
        link_val = video_id if video_id else raw_video_input
        tour_type = 'YOUTUBE' if len(link_val) == 11 and not ('/' in link_val) else ('EMBED' if link_val else 'FILE')
        cur.execute("""
            UPDATE virtual_tours 
            SET title=%s, description=%s, video_id=%s, tour_type=%s, category=%s, is_active=%s 
            WHERE id=%s
        """, (title, description, link_val, tour_type, category, is_active, id))

    mysql.connection.commit()
    cur.close()
    flash('Virtual tour entry updated successfully.', 'success')
    return redirect(url_for('admin_virtual_tour'))


@app.route('/admin/virtual-tour/delete/<int:id>', methods=['POST'])
@admin_required
def admin_delete_virtual_tour(id):
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM virtual_tours WHERE id = %s", (id,))
    mysql.connection.commit()
    cur.close()
    flash('Virtual tour deleted.', 'info')
    return redirect(url_for('admin_virtual_tour'))

# --- ABOUT US CRUD (Page 9) ---
@app.route('/admin/about')
@admin_required
def admin_about():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM about_sections ORDER BY id ASC")
    about_items = cur.fetchall()
    cur.close()

    org_chart_image = get_site_setting('org_chart_image', '')
    admission_file = get_site_setting('admission_form_file', '')

    return render_template(
        'admin/about.html',
        about_items=about_items,
        org_chart_image=org_chart_image,
        admission_file=admission_file
    )

@app.route('/admin/about/admission-file/update', methods=['POST'])
@admin_required
def admin_update_admission_file():
    uploaded_file = save_uploaded_file(request.files.get('admission_file'))
    if uploaded_file:
        cur = mysql.connection.cursor()
        cur.execute("""
            INSERT INTO site_settings (setting_key, setting_value) 
            VALUES ('admission_form_file', %s)
            ON DUPLICATE KEY UPDATE setting_value=%s
        """, (uploaded_file, uploaded_file))
        mysql.connection.commit()
        cur.close()
        flash('Admission form document updated successfully.', 'success')
    else:
        flash('Please select a valid PDF or document file.', 'warning')
    return redirect(url_for('admin_about'))


@app.route('/admin/about/admission-file/delete', methods=['POST'])
@admin_required
def admin_delete_admission_file():
    cur = mysql.connection.cursor()
    cur.execute("UPDATE site_settings SET setting_value='' WHERE setting_key='admission_form_file'")
    mysql.connection.commit()
    cur.close()
    flash('Admission form file has been removed.', 'info')
    return redirect(url_for('admin_about'))

@app.route('/admin/about/org-chart/update', methods=['POST'])
@admin_required
def admin_update_org_chart():
    img = save_uploaded_file(request.files.get('org_chart_image'))
    if img:
        cur = mysql.connection.cursor()
        cur.execute("""
            INSERT INTO site_settings (setting_key, setting_value) 
            VALUES ('org_chart_image', %s)
            ON DUPLICATE KEY UPDATE setting_value=%s
        """, (img, img))
        mysql.connection.commit()
        cur.close()
        flash('Organizational chart image updated successfully.', 'success')
    else:
        flash('Please select a valid image file (PNG, JPG, JPEG, WebP).', 'warning')
    return redirect(url_for('admin_about'))

@app.route('/admin/about/create', methods=['POST'])
@admin_required
def admin_create_about():
    title = request.form.get('title')
    body = request.form.get('body')

    cur = mysql.connection.cursor()
    cur.execute(
        "INSERT INTO about_sections (title, body, is_active) VALUES (%s, %s, 1)",
        (title, body)
    )
    mysql.connection.commit()
    cur.close()
    flash('About statement created successfully.', 'success')
    return redirect(url_for('admin_about'))


@app.route('/admin/about/edit/<int:id>', methods=['POST'])
@admin_required
def admin_edit_about(id):
    title = request.form.get('title')
    body = request.form.get('body')
    is_active = 1 if request.form.get('is_active') in ['on', '1', 1, True] else 0

    cur = mysql.connection.cursor()
    cur.execute("""
        UPDATE about_sections 
        SET title=%s, body=%s, is_active=%s 
        WHERE id=%s
    """, (title, body, is_active, id))

    mysql.connection.commit()
    cur.close()
    flash('About statement updated successfully.', 'success')
    return redirect(url_for('admin_about'))


@app.route('/admin/about/delete/<int:id>', methods=['POST'])
@admin_required
def admin_delete_about(id):
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM about_sections WHERE id = %s", (id,))
    mysql.connection.commit()
    cur.close()
    flash('About section removed.', 'info')
    return redirect(url_for('admin_about'))

# --- SITE SETTINGS ---
@app.route('/admin/site-settings')
@admin_required
def admin_site_settings():
    cur = mysql.connection.cursor()
    cur.execute("SELECT setting_key, setting_value FROM site_settings")
    rows = cur.fetchall()
    cur.close()
    
    settings = {r['setting_key']: r['setting_value'] for r in rows}

    return render_template(
        'admin/site_settings.html',
        settings=settings,
        hero_image=settings.get('hero_banner_image', ''),
        hero_title=settings.get('hero_title', 'KOLEHIYO NG LUNGSOD NG LIPA'),
        hero_subtitle=settings.get('hero_subtitle', 'Your Gateway to Academic Excellence · Quality Education for All')
    )


@app.route('/admin/site-settings/update', methods=['POST'])
@admin_required
def admin_update_site_settings():
    fields = [
        'hero_title', 'hero_subtitle',
        'top_phone', 'top_email',
        'facebook_url', 'twitter_url', 'linkedin_url', 'instagram_url',
        'footer_address', 'footer_phone', 'footer_email', 'footer_map_url'
    ]

    cur = mysql.connection.cursor()

    for field in fields:
        val = request.form.get(field)
        if val is not None:
            # Clean map url if it's the map field
            if field == 'footer_map_url':
                val = clean_map_url(val)
            cur.execute("""
                INSERT INTO site_settings (setting_key, setting_value) VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE setting_value=%s
            """, (field, val, val))

    # Hero Image upload
    img = save_uploaded_file(request.files.get('hero_banner_image'))
    if img:
        cur.execute("""
            INSERT INTO site_settings (setting_key, setting_value) VALUES ('hero_banner_image', %s)
            ON DUPLICATE KEY UPDATE setting_value=%s
        """, (img, img))

    mysql.connection.commit()
    cur.close()

    flash('Site settings, contacts, and map link updated successfully.', 'success')
    return redirect(url_for('admin_site_settings'))

# --- ADMIN INQUIRIES/FEEDBACK ---
@app.route('/admin/inquiries')
@admin_required
def admin_inquiries():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM inquiries ORDER BY created_at DESC")
    inquiries = cur.fetchall()
    cur.close()
    return render_template('admin/inquiries.html', inquiries=inquiries)

@app.route('/admin/inquiries/delete/<int:id>', methods=['POST'])
@admin_required
def admin_delete_inquiry(id):
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM inquiries WHERE id = %s", (id,))
    mysql.connection.commit()
    cur.close()
    flash('Inquiry deleted.', 'info')
    return redirect(url_for('admin_inquiries'))

@app.route('/admin/inquiries/clear', methods=['POST'])
@admin_required
def admin_clear_inquiries():
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM inquiries")
    mysql.connection.commit()
    cur.close()
    flash('All inquiries cleared.', 'info')
    return redirect(url_for('admin_inquiries'))

# --- CALENDAR API ---
@app.route('/api/calendar-events')
def calendar_events():
    cur = mysql.connection.cursor()
    cur.execute("SELECT id, title, description, start_date, end_date FROM academic_calendar WHERE is_active = 1 ORDER BY start_date ASC")
    rows = cur.fetchall()
    cur.close()
    events = []
    for r in rows:
        ev = {
            'id': r['id'],
            'title': r['title'],
            'start': r['start_date'].strftime('%Y-%m-%d') if r['start_date'] else None,
            'end': (r['end_date'].strftime('%Y-%m-%d') if r['end_date'] else r['start_date'].strftime('%Y-%m-%d')) if r['start_date'] else None,
            'description': r['description'] or ''
        }
        events.append(ev)
    return jsonify(events)

# ==================== EMERGENCY ADVISORIES ==================== #
@app.context_processor
def inject_emergency_advisories():
    try:
        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM emergency_advisories WHERE is_active = 1 AND (expires_at IS NULL OR expires_at > NOW()) ORDER BY id DESC LIMIT 3")
        advisories = cur.fetchall()
        cur.close()
        return {'active_advisories': advisories}
    except Exception:
        return {'active_advisories': []}

@app.route('/admin/advisories')
@admin_required
def admin_advisories():
    from datetime import datetime
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM emergency_advisories ORDER BY id DESC")
    advisories = cur.fetchall()
    cur.close()
    now_str = datetime.now().strftime('%Y-%m-%dT%H:%M')
    return render_template('admin/advisories.html', advisories=advisories, now_str=now_str)

@app.route('/admin/advisories/create', methods=['POST'])
@admin_required
def admin_create_advisory():
    title = request.form.get('title')
    message = request.form.get('message')
    advisory_type = request.form.get('advisory_type', 'info')
    expires_at = request.form.get('expires_at') or None
    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO emergency_advisories (title, message, advisory_type, is_active, expires_at) VALUES (%s, %s, %s, 1, %s)",
                (title, message, advisory_type, expires_at))
    mysql.connection.commit()
    cur.close()
    flash('Advisory created successfully.', 'success')
    return redirect(url_for('admin_advisories'))

@app.route('/admin/advisories/edit/<int:id>', methods=['POST'])
@admin_required
def admin_edit_advisory(id):
    title = request.form.get('title')
    message = request.form.get('message')
    advisory_type = request.form.get('advisory_type', 'info')
    is_active = 1 if request.form.get('is_active') in ['1', 'on', True] else 0
    expires_at = request.form.get('expires_at') or None
    cur = mysql.connection.cursor()
    cur.execute("UPDATE emergency_advisories SET title=%s, message=%s, advisory_type=%s, is_active=%s, expires_at=%s WHERE id=%s",
                (title, message, advisory_type, is_active, expires_at, id))
    mysql.connection.commit()
    cur.close()
    flash('Advisory updated.', 'success')
    return redirect(url_for('admin_advisories'))

@app.route('/admin/advisories/delete/<int:id>', methods=['POST'])
@admin_required
def admin_delete_advisory(id):
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM emergency_advisories WHERE id = %s", (id,))
    mysql.connection.commit()
    cur.close()
    flash('Advisory deleted.', 'info')
    return redirect(url_for('admin_advisories'))

# ====================================================================== #
#  ENROLLMENT: PROGRAM ASSIGNMENT (service layer + admin routes)          #
# ====================================================================== #
# Data model (existing `enrollments` table, extended by the migration):
#   * one CURRENT row per student  -> is_current = 1, enrollment_status 'Enrolled'
#   * history rows are kept        -> is_current = NULL, status 'Transferred' / 'Withdrawn'
#   * UNIQUE (student_id, is_current) makes a second current row impossible.
ENROLLMENT_STATUSES = ('Enrolled', 'Transferred', 'Withdrawn')


class ProgramAssignmentError(Exception):
    """A validation problem with a program assignment (message is shown to the admin)."""


def parse_program_id(raw):
    """'' -> None (no program); '12' -> 12; anything else is rejected."""
    raw = (raw or '').strip()
    if raw == '':
        return None
    if not re.fullmatch(r'[0-9]{1,10}', raw):
        raise ProgramAssignmentError('Invalid academic program selected.')
    return int(raw)


def get_active_programs(cur):
    cur.execute("SELECT id, course_code, course_name FROM courses WHERE is_active = 1 ORDER BY course_name ASC")
    return cur.fetchall()


def set_student_program(cur, student_id, course_id, admin_id):
    """Assign / change / remove a student's CURRENT program inside the caller's transaction.

    The caller commits or rolls back. Returns one of
    'assigned' | 'changed' | 'removed' | 'unchanged'.
    """
    # Lock the student row so two admins cannot race each other.
    cur.execute("SELECT id, role FROM users WHERE id = %s FOR UPDATE", (student_id,))
    student = cur.fetchone()
    if not student:
        raise ProgramAssignmentError('Student account not found.')
    if student['role'] != 'STUDENT':
        raise ProgramAssignmentError('Academic programs can only be assigned to student accounts.')

    cur.execute("SELECT id, course_id FROM enrollments WHERE student_id = %s AND is_current = 1", (student_id,))
    current = cur.fetchone()

    if course_id is None:                               # remove assignment
        if not current:
            return 'unchanged'
        cur.execute("""UPDATE enrollments
                       SET enrollment_status = 'Withdrawn', is_current = NULL, updated_at = NOW()
                       WHERE id = %s""", (current['id'],))
        return 'removed'

    cur.execute("SELECT id, is_active FROM courses WHERE id = %s", (course_id,))
    course = cur.fetchone()
    if not course:
        raise ProgramAssignmentError('The selected academic program does not exist.')

    if current and current['course_id'] == course_id:   # nothing to do: keep the existing assignment
        return 'unchanged'
    if not course['is_active']:
        raise ProgramAssignmentError('The selected academic program is not active.')

    if current:                                         # keep history, never overwrite
        cur.execute("""UPDATE enrollments
                       SET enrollment_status = 'Transferred', is_current = NULL, updated_at = NOW()
                       WHERE id = %s""", (current['id'],))
    cur.execute("""INSERT INTO enrollments (student_id, course_id, enrollment_status, is_current, assigned_by)
                   VALUES (%s, %s, 'Enrolled', 1, %s)""", (student_id, course_id, admin_id))
    return 'changed' if current else 'assigned'


def _run_program_change(student_id, raw_course_id):
    """Transactional wrapper used by the stand-alone assign/remove routes."""
    cur = mysql.connection.cursor()
    try:
        result = set_student_program(cur, student_id, parse_program_id(raw_course_id), session['user_id'])
        mysql.connection.commit()
        return result, None
    except ProgramAssignmentError as err:
        mysql.connection.rollback()
        return None, str(err)
    except DBIntegrityError:
        mysql.connection.rollback()
        return None, 'This student already has an active enrollment. Please refresh the page and try again.'
    except Exception:
        mysql.connection.rollback()
        app.logger.exception('program assignment failed')
        return None, 'Could not update the academic program because of a server error. Nothing was changed.'
    finally:
        cur.close()


@app.route('/admin/students/<int:student_id>/program', methods=['POST'])
@admin_required
def admin_set_student_program(student_id):
    """Assign or change a student's program (an empty value removes it)."""
    result, error = _run_program_change(student_id, request.form.get('course_id'))
    if error:
        flash(error, 'danger')
    elif result == 'unchanged':
        flash('No change: the student already has that academic program.', 'info')
    elif result == 'removed':
        flash('Academic program assignment removed.', 'info')
    else:
        flash('Student academic program successfully updated.', 'success')
    return redirect(_safe_back('admin_enrollments'))


@app.route('/admin/students/<int:student_id>/program/remove', methods=['POST'])
@admin_required
def admin_remove_student_program(student_id):
    result, error = _run_program_change(student_id, '')
    if error:
        flash(error, 'danger')
    elif result == 'unchanged':
        flash('This student has no assigned program.', 'info')
    else:
        flash('Academic program assignment removed.', 'info')
    return redirect(_safe_back('admin_enrollments'))


# ---------------------------------------------------------------------- #
#  STUDENT ENROLLMENT MANAGEMENT (admin list, filters, summary)          #
# ---------------------------------------------------------------------- #
ENROLLMENTS_PER_PAGE = 15
ENROLLMENT_SORTS = {
    'newest': 'e.enrolled_at DESC, e.id DESC',
    'oldest': 'e.enrolled_at ASC, e.id ASC',
    'name':   'u.name ASC, e.id DESC',
}


def _like_pattern(term):
    """Escape LIKE wildcards ('|' is the escape char - portable across SQL engines)."""
    term = term.replace('|', '||').replace('%', '|%').replace('_', '|_')
    return f'%{term}%'


@app.route('/admin/enrollments')
@admin_required
def admin_enrollments():
    raw_program = (request.args.get('program') or '').strip()
    q = (request.args.get('q') or '').strip()[:100]
    status = request.args.get('status') or 'Enrolled'
    sort = request.args.get('sort') or 'newest'
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1

    program_id = int(raw_program) if re.fullmatch(r'[0-9]{1,10}', raw_program) else None
    if status != 'All' and status not in ENROLLMENT_STATUSES:
        status = 'Enrolled'
    if sort not in ENROLLMENT_SORTS:
        sort = 'newest'

    where = ["u.role = 'STUDENT'"]
    params = []
    if status != 'All':
        where.append("e.enrollment_status = %s")
        params.append(status)
    if program_id is not None:
        where.append("e.course_id = %s")
        params.append(program_id)
    if q:
        where.append("(u.name LIKE %s ESCAPE '|' OR u.email LIKE %s ESCAPE '|')")
        params.extend([_like_pattern(q), _like_pattern(q)])
    where_sql = ' AND '.join(where)

    cur = mysql.connection.cursor()

    cur.execute(f"""SELECT COUNT(*) AS n
                    FROM enrollments e
                    JOIN users u   ON u.id = e.student_id
                    JOIN courses c ON c.id = e.course_id
                    WHERE {where_sql}""", params)
    total = cur.fetchone()['n']
    total_pages = max(1, -(-total // ENROLLMENTS_PER_PAGE))
    page = min(page, total_pages)

    cur.execute(f"""SELECT e.id AS enrollment_id, e.enrolled_at, e.enrollment_status, e.is_current,
                           u.id AS student_id, u.name, u.email, u.phone_number, u.status AS account_status,
                           c.id AS course_id, c.course_code, c.course_name, c.is_active AS course_active
                    FROM enrollments e
                    JOIN users u   ON u.id = e.student_id
                    JOIN courses c ON c.id = e.course_id
                    WHERE {where_sql}
                    ORDER BY {ENROLLMENT_SORTS[sort]}
                    LIMIT %s OFFSET %s""",
                params + [ENROLLMENTS_PER_PAGE, (page - 1) * ENROLLMENTS_PER_PAGE])
    rows = cur.fetchall()

    # ---- summary cards (all real counts) ----
    cur.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'STUDENT'")
    total_students = cur.fetchone()['n']
    cur.execute("""SELECT COUNT(*) AS n
                   FROM users u
                   WHERE u.role = 'STUDENT'
                     AND EXISTS (SELECT 1 FROM enrollments e
                                 WHERE e.student_id = u.id AND e.is_current = 1
                                   AND e.enrollment_status = 'Enrolled')""")
    total_enrolled = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM courses WHERE is_active = 1")
    active_programs = cur.fetchone()['n']

    cur.execute("""SELECT c.id, c.course_code, c.course_name, c.is_active, COUNT(u.id) AS student_count
                   FROM courses c
                   LEFT JOIN enrollments e ON e.course_id = c.id AND e.is_current = 1
                                          AND e.enrollment_status = 'Enrolled'
                   LEFT JOIN users u ON u.id = e.student_id AND u.role = 'STUDENT'
                   GROUP BY c.id, c.course_code, c.course_name, c.is_active
                   ORDER BY c.course_name ASC""")
    per_program = cur.fetchall()
    programs = get_active_programs(cur)
    cur.close()

    # Only active programs can be assigned; keep ids to build the filter list of ALL programs.
    qs = {k: v for k, v in (('program', str(program_id) if program_id is not None else ''),
                            ('q', q), ('status', status), ('sort', sort)) if v}
    return render_template(
        'admin/enrollments.html',
        rows=rows, total=total, page=page, total_pages=total_pages,
        per_page=ENROLLMENTS_PER_PAGE,
        total_students=total_students, total_enrolled=total_enrolled,
        without_program=total_students - total_enrolled, active_programs=active_programs,
        per_program=per_program, programs=programs,
        filters={'program': program_id, 'q': q, 'status': status, 'sort': sort},
        qs=qs, statuses=ENROLLMENT_STATUSES
    )


# ====================================================================== #
#  PROSPECTUS / CURRICULUM DOCUMENTS                                      #
# ====================================================================== #
PROSPECTUS_MIME = {
    'pdf':  'application/pdf',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'doc':  'application/msword',
}
# MIME types a browser may legitimately report for each extension.
_CLIENT_MIME_OK = {
    'pdf':  {'application/pdf', 'application/x-pdf', 'application/octet-stream'},
    'docx': {PROSPECTUS_MIME['docx'], 'application/octet-stream'},
    'doc':  {'application/msword', 'application/octet-stream'},
}
_ACADEMIC_YEAR_RE = re.compile(r'^[0-9A-Za-z][0-9A-Za-z /\-]{0,19}$')


def validate_prospectus_upload(file_obj):
    """Treat the upload as untrusted. Returns (ext, bytes, clean_original_name) or raises ValueError."""
    if not file_obj or not file_obj.filename:
        raise ValueError('Please choose a prospectus file to upload.')

    original = file_obj.filename.replace('\\', '/').rsplit('/', 1)[-1]
    ext = original.rsplit('.', 1)[-1].lower() if '.' in original else ''
    if ext not in app.config['PROSPECTUS_ALLOWED_EXTENSIONS']:
        raise ValueError('Invalid file type. Only PDF, DOCX or DOC files can be uploaded.')

    client_mime = (file_obj.mimetype or 'application/octet-stream').lower()
    if client_mime not in _CLIENT_MIME_OK[ext]:
        raise ValueError('The file type reported by your browser does not match its extension.')

    limit = app.config['PROSPECTUS_MAX_BYTES']
    data = file_obj.stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f'The file is too large. The maximum size is {limit // (1024 * 1024)} MB.')
    if not data:
        raise ValueError('The uploaded file is empty.')

    # Content sniffing: the bytes must really be the claimed format (extension/MIME can be faked).
    if ext == 'pdf':
        valid = b'%PDF-' in data[:1024]
    elif ext == 'doc':
        valid = data.startswith(bytes.fromhex('D0CF11E0A1B11AE1'))
    else:  # docx = zip container with the Word parts
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
            valid = '[Content_Types].xml' in names and 'word/document.xml' in names
        except zipfile.BadZipFile:
            valid = False
    if not valid:
        raise ValueError('The file content is not a valid %s document.' % ext.upper())

    clean_name = (secure_filename(original) or f'prospectus.{ext}')[:200]
    return ext, data, clean_name


def _prospectus_root():
    return os.path.realpath(app.config['PROSPECTUS_FOLDER'])


def _resolve_prospectus_path(rel_path):
    """Absolute path of a stored file, or None if it escapes the storage root / is missing."""
    root = _prospectus_root()
    full = os.path.realpath(os.path.join(root, rel_path or ''))
    try:
        if os.path.commonpath([root, full]) != root:
            return None
    except ValueError:                      # e.g. different drive on Windows
        return None
    return full if os.path.isfile(full) else None


def _write_prospectus_file(rel_path, data):
    full = os.path.join(_prospectus_root(), rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    tmp = full + '.part'
    with open(tmp, 'wb') as fh:
        fh.write(data)
    os.replace(tmp, full)


def _remove_prospectus_file(rel_path):
    full = _resolve_prospectus_path(rel_path)
    if full:
        try:
            os.remove(full)
        except OSError as err:
            app.logger.warning('Could not remove prospectus file %s: %s', rel_path, err)


def _send_prospectus(doc, inline):
    """Stream a stored document. `doc` comes from the database, never from the browser."""
    full = _resolve_prospectus_path(doc['file_path'])
    if not full:
        abort(404)
    ext = doc['file_type']
    base = secure_filename(f"{doc['course_code']}_{doc['document_title']}_v{doc['version']}") or 'prospectus'
    resp = send_file(full, mimetype=PROSPECTUS_MIME.get(ext, 'application/octet-stream'),
                     as_attachment=not (inline and ext == 'pdf'),
                     download_name=f'{base}.{ext}', conditional=True, max_age=0)
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Cache-Control'] = 'private, no-cache'
    return resp


_DOC_COLUMNS = """d.id, d.course_id, d.document_title, d.original_filename, d.file_path, d.file_type,
                  d.file_size, d.version, d.academic_year, d.is_current, d.is_published, d.created_at,
                  c.course_code, c.course_name"""


def get_student_enrollment(cur, student_id):
    """The student's own current program (or None)."""
    cur.execute("""SELECT e.id AS enrollment_id, e.enrolled_at, e.enrollment_status, e.academic_year, e.semester,
                          c.id AS course_id, c.course_code, c.course_name
                   FROM enrollments e
                   JOIN courses c ON c.id = e.course_id
                   WHERE e.student_id = %s AND e.is_current = 1 AND e.enrollment_status = 'Enrolled'""",
                (student_id,))
    return cur.fetchone()


def get_student_prospectus(cur, student_id):
    """The published, current prospectus of the student's OWN program (or None)."""
    cur.execute(f"""SELECT {_DOC_COLUMNS}
                    FROM enrollments e
                    JOIN course_documents d ON d.course_id = e.course_id
                    JOIN courses c          ON c.id = d.course_id
                    WHERE e.student_id = %s AND e.is_current = 1 AND e.enrollment_status = 'Enrolled'
                      AND d.is_current = 1 AND d.is_published = 1""", (student_id,))
    return cur.fetchone()


def _get_document(doc_id):
    cur = mysql.connection.cursor()
    cur.execute(f"""SELECT {_DOC_COLUMNS}
                    FROM course_documents d JOIN courses c ON c.id = d.course_id
                    WHERE d.id = %s""", (doc_id,))
    doc = cur.fetchone()
    cur.close()
    return doc


# ---- student side: no id in the URL, the program comes from the logged-in student's record ----
def _student_prospectus_response(inline):
    if session.get('role') != 'STUDENT':
        abort(403)
    cur = mysql.connection.cursor()
    doc = get_student_prospectus(cur, session['user_id'])
    cur.close()
    if not doc:
        abort(404)
    return _send_prospectus(doc, inline)


@app.route('/student/prospectus/view')
@login_required
def student_prospectus_view():
    return _student_prospectus_response(inline=True)


@app.route('/student/prospectus/download')
@login_required
def student_prospectus_download():
    return _student_prospectus_response(inline=False)


# ---- admin side ----
@app.route('/admin/courses/documents/<int:doc_id>/view')
@admin_required
def admin_view_prospectus(doc_id):
    doc = _get_document(doc_id)
    if not doc:
        abort(404)
    return _send_prospectus(doc, inline=True)


@app.route('/admin/courses/documents/<int:doc_id>/download')
@admin_required
def admin_download_prospectus(doc_id):
    doc = _get_document(doc_id)
    if not doc:
        abort(404)
    return _send_prospectus(doc, inline=False)


@app.route('/admin/courses/<int:course_id>/documents/upload', methods=['POST'])
@admin_required
def admin_upload_prospectus(course_id):
    """Upload a NEW version for a program (also used for 'Replace': older versions are archived, not deleted)."""
    back = url_for('admin_courses')
    try:
        ext, data, original_name = validate_prospectus_upload(request.files.get('prospectus_file'))
    except ValueError as err:
        flash(str(err), 'danger')
        return redirect(back)

    title = (request.form.get('document_title') or '').strip()[:255]
    academic_year = (request.form.get('academic_year') or '').strip()
    if academic_year and not _ACADEMIC_YEAR_RE.match(academic_year):
        flash('Academic year may only contain letters, numbers, spaces, "-" or "/" (max 20 characters).', 'danger')
        return redirect(back)
    publish = (request.form.get('publish_mode') or 'publish') != 'draft'

    cur = mysql.connection.cursor()
    rel_path = None
    try:
        cur.execute("SELECT id, course_code FROM courses WHERE id = %s FOR UPDATE", (course_id,))
        course = cur.fetchone()
        if not course:
            mysql.connection.rollback()
            flash('Academic program not found.', 'danger')
            return redirect(back)

        cur.execute("SELECT COALESCE(MAX(version), 0) + 1 AS v FROM course_documents WHERE course_id = %s", (course_id,))
        version = int(cur.fetchone()['v'])
        if not title:
            title = f"{course['course_code']} Curriculum {academic_year or 'v%d' % version}"

        rel_path = f"course_{course_id}/{uuid.uuid4().hex}.{ext}"
        _write_prospectus_file(rel_path, data)

        if publish:   # previous current document becomes an archived version
            cur.execute("""UPDATE course_documents SET is_current = NULL, updated_at = NOW()
                           WHERE course_id = %s AND is_current = 1""", (course_id,))
        cur.execute("""INSERT INTO course_documents
                         (course_id, document_title, original_filename, stored_filename, file_path, file_type,
                          file_size, version, academic_year, is_current, is_published, uploaded_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (course_id, title, original_name, rel_path.split('/')[-1], rel_path, ext,
                     len(data), version, academic_year or None, 1 if publish else None,
                     1 if publish else 0, session['user_id']))
        mysql.connection.commit()
    except Exception:
        mysql.connection.rollback()
        if rel_path:
            _remove_prospectus_file(rel_path)
        app.logger.exception('prospectus upload failed')
        flash('The prospectus could not be saved because of a server error. Nothing was changed.', 'danger')
        return redirect(back)
    finally:
        cur.close()

    if publish:
        flash(f"Prospectus v{version} uploaded and published as the current curriculum for {course['course_code']}.", 'success')
    else:
        flash(f"Prospectus v{version} saved as a draft for {course['course_code']}. Publish it when it is ready.", 'success')
    return redirect(back)


@app.route('/admin/courses/documents/<int:doc_id>/publish', methods=['POST'])
@admin_required
def admin_publish_prospectus(doc_id):
    """Make a (draft or archived) version the current, student-visible curriculum."""
    doc = _get_document(doc_id)
    if not doc:
        flash('Document not found.', 'danger')
        return redirect(url_for('admin_courses'))
    cur = mysql.connection.cursor()
    try:
        cur.execute("SELECT id FROM courses WHERE id = %s FOR UPDATE", (doc['course_id'],))
        cur.execute("""UPDATE course_documents SET is_current = NULL, updated_at = NOW()
                       WHERE course_id = %s AND is_current = 1""", (doc['course_id'],))
        cur.execute("""UPDATE course_documents SET is_current = 1, is_published = 1, updated_at = NOW()
                       WHERE id = %s""", (doc_id,))
        mysql.connection.commit()
    except Exception:
        mysql.connection.rollback()
        app.logger.exception('prospectus publish failed')
        flash('Could not publish the document. Nothing was changed.', 'danger')
        return redirect(url_for('admin_courses'))
    finally:
        cur.close()
    flash(f"Version {doc['version']} is now the current published prospectus for {doc['course_code']}.", 'success')
    return redirect(url_for('admin_courses'))


@app.route('/admin/courses/documents/<int:doc_id>/archive', methods=['POST'])
@admin_required
def admin_archive_prospectus(doc_id):
    """Withdraw the current version from students but keep the file and its history."""
    doc = _get_document(doc_id)
    if not doc:
        flash('Document not found.', 'danger')
        return redirect(url_for('admin_courses'))
    cur = mysql.connection.cursor()
    cur.execute("UPDATE course_documents SET is_current = NULL, updated_at = NOW() WHERE id = %s", (doc_id,))
    mysql.connection.commit()
    cur.close()
    flash(f"Version {doc['version']} archived. Students no longer see it.", 'info')
    return redirect(url_for('admin_courses'))


@app.route('/admin/courses/documents/<int:doc_id>/delete', methods=['POST'])
@admin_required
def admin_delete_prospectus(doc_id):
    doc = _get_document(doc_id)
    if not doc:
        flash('Document not found.', 'danger')
        return redirect(url_for('admin_courses'))
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM course_documents WHERE id = %s", (doc_id,))
    mysql.connection.commit()
    cur.close()
    _remove_prospectus_file(doc['file_path'])
    flash(f"Version {doc['version']} of the {doc['course_code']} prospectus was permanently deleted.", 'info')
    return redirect(url_for('admin_courses'))


if __name__ == '__main__':
    app.run(debug=True, port=5000)