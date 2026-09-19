import os
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_mysqldb import MySQL
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from config import Config

app = Flask(__name__)
app.config.from_object(Config)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
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
    # 1. Active Courses
    cur.execute("SELECT * FROM courses WHERE is_active = 1 ORDER BY course_name ASC")
    courses = cur.fetchall()

    # 2. Upcoming Academic Events
    cur.execute("SELECT * FROM academic_calendar WHERE is_active = 1 ORDER BY start_date ASC LIMIT 5")
    events = cur.fetchall()

    # 3. Total active students count
    cur.execute("SELECT COUNT(*) as student_count FROM users WHERE role = 'STUDENT' AND status = 'Active'")
    student_count = cur.fetchone()['student_count']

    cur.close()

    return render_template(
        'portal/teacher_dashboard.html',
        courses=courses,
        events=events,
        student_count=student_count
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

    enrolled_courses = []
    recommendations = []

    if user:
        # 1. Fetch only courses the current user is ACTUALLY enrolled in
        try:
            cur.execute("""
                SELECT c.id, c.course_code, c.course_name, c.description, e.enrolled_at
                FROM enrollments e
                JOIN courses c ON e.course_id = c.id
                WHERE e.student_id = %s
                ORDER BY e.enrolled_at DESC
            """, (user['id'],))
            enrolled_courses = cur.fetchall()
        except Exception as err:
            print(f"Enrollment query error: {err}")
            enrolled_courses = []

        # 2. ONLY generate recommendations IF the student is ACTUALLY enrolled
        if enrolled_courses:
            # Recommend student clubs
            cur.execute("SELECT id, name, details FROM clubs WHERE is_active = 1 LIMIT 2")
            clubs = cur.fetchall()
            for club in clubs:
                recommendations.append({
                    'icon': 'fa-users',
                    'color': '#10b981',
                    'title': club['name'],
                    'desc': (club['details'][:80] + '...') if club['details'] and len(club['details']) > 80 else (club['details'] or 'Join this student organization.'),
                    'link': url_for('clubs'),
                    'badge': 'Club'
                })

            # Recommend latest campus bulletins
            cur.execute("SELECT id, title, content FROM news WHERE is_active = 1 ORDER BY id DESC LIMIT 2")
            news_items = cur.fetchall()
            for article in news_items:
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
        enrolled_courses=enrolled_courses,
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

    # 6. Enrollment count per course (bar chart)
    cur.execute("""
        SELECT c.course_code, c.course_name, COUNT(e.id) AS enroll_count
        FROM courses c
        LEFT JOIN enrollments e ON c.id = e.course_id
        WHERE c.is_active = 1
        GROUP BY c.id, c.course_code, c.course_name
        ORDER BY enroll_count DESC
    """)
    enrollment_data = cur.fetchall()
    enrollment_labels = [r['course_code'] for r in enrollment_data]
    enrollment_counts = [r['enroll_count'] for r in enrollment_data]

    # 7. Student registrations per month (line chart - last 12 months)
    cur.execute("""
        SELECT DATE_FORMAT(created_at, '%%Y-%%m') AS month_label,
               COUNT(*) AS reg_count
        FROM users
        WHERE role = 'STUDENT'
          AND created_at >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
        GROUP BY month_label
        ORDER BY month_label ASC
    """)
    monthly_data = cur.fetchall()

    # Build full 12-month series
    from datetime import datetime, timedelta
    import calendar
    month_series = []
    now = datetime.now()
    for i in range(11, -1, -1):
        d = now - timedelta(days=i * 30)
        key = d.strftime('%Y-%m')
        label = d.strftime('%b %Y')
        month_series.append({'key': key, 'label': label, 'count': 0})

    reg_map = {r['month_label']: r['reg_count'] for r in monthly_data}
    for m in month_series:
        if m['key'] in reg_map:
            m['count'] = reg_map[m['key']]

    monthly_labels = [m['label'] for m in month_series]
    monthly_counts = [m['count'] for m in month_series]

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
        enrollment_labels=enrollment_labels,
        enrollment_counts=enrollment_counts,
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
@app.route('/admin/users')
@admin_required
def admin_users():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM users ORDER BY id DESC")
    users = cur.fetchall()
    cur.close()
    return render_template('admin/users.html', users=users)

@app.route('/admin/users/create', methods=['POST'])
@admin_required
def admin_create_user():
    name = request.form.get('name')
    email = request.form.get('email')
    password = request.form.get('password')
    phone = request.form.get('phone_number')
    role = request.form.get('role')
    pw_hash = generate_password_hash(password)

    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO users (name, email, password_hash, phone_number, role, status) VALUES (%s, %s, %s, %s, %s, 'Active')",
                (name, email, pw_hash, phone, role))
    mysql.connection.commit()
    cur.close()
    flash('User registered.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/edit/<int:id>', methods=['POST'])
@admin_required
def admin_edit_user(id):
    name = request.form.get('name')
    email = request.form.get('email')
    phone = request.form.get('phone_number')
    role = request.form.get('role')
    status = request.form.get('status')
    password = request.form.get('password')

    cur = mysql.connection.cursor()
    if password:
        pw_hash = generate_password_hash(password)
        cur.execute("UPDATE users SET name=%s, email=%s, phone_number=%s, role=%s, status=%s, password_hash=%s WHERE id=%s",
                    (name, email, phone, role, status, pw_hash, id))
    else:
        cur.execute("UPDATE users SET name=%s, email=%s, phone_number=%s, role=%s, status=%s WHERE id=%s",
                    (name, email, phone, role, status, id))
    mysql.connection.commit()
    cur.close()
    flash('User updated.', 'success')
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
    cur.close()
    return render_template('admin/courses.html', courses=courses)


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
    cur.execute("DELETE FROM courses WHERE id = %s", (id,))
    mysql.connection.commit()
    cur.close()
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

if __name__ == '__main__':
    app.run(debug=True, port=5000)