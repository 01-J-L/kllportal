"""End-to-end tests for student program assignment, enrollment management and prospectus handling.

Run:  python -m pytest tests -v        (no MySQL needed - see tests/_shims.py)
"""
import io
import os
import re
import sys
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ['KLL_AUTO_MIGRATE'] = '0'

import _shims  # noqa: E402
MySQLShim = _shims.install()
from werkzeug.security import generate_password_hash  # noqa: E402
import app as appmod  # noqa: E402

PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n' + b'A' * 2048
PDF2 = b'%PDF-1.7\n%second version\n' + b'B' * 4096


def make_docx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', '<w:document/>')
    return buf.getvalue()


@pytest.fixture()
def env(tmp_path):
    MySQLShim.reset()
    app = appmod.app
    app.config.update(TESTING=True, PROSPECTUS_FOLDER=str(tmp_path / 'protected'),
                      PROSPECTUS_MAX_BYTES=10 * 1024 * 1024)
    os.makedirs(app.config['PROSPECTUS_FOLDER'], exist_ok=True)
    with app.app_context():
        conn = appmod.mysql.connection._c
        conn.executescript(_shims.SCHEMA)
        pw = generate_password_hash('secret123')
        conn.executemany("INSERT INTO users (name,email,password_hash,phone_number,role,status) VALUES (?,?,?,?,?,?)", [
            ('Admin One', 'admin@kll.test', pw, '0911', 'ADMIN', 'Active'),            # 1
            ('Prof Teacher', 'teacher@kll.test', pw, '0922', 'TEACHER', 'Active'),     # 2
            ('Juan Dela Cruz', 'juan@kll.test', pw, '0933', 'STUDENT', 'Active'),      # 3
            ('Maria Santos', 'maria@kll.test', pw, None, 'STUDENT', 'Active'),         # 4
        ])
        conn.executemany("INSERT INTO courses (course_code,course_name,is_active) VALUES (?,?,?)", [
            ('BSCS', 'BS Computer Science', 1),                    # 1
            ('BPA', 'Bachelor of Public Administration', 1),       # 2
            ('BSCRIM', 'Bachelor of Science in Criminology', 1),   # 3
            ('OLD', 'Retired Program', 0),                         # 4 (inactive)
        ])
        conn.commit()
    yield app
    MySQLShim.reset()


def db(sql, args=()):
    with appmod.app.app_context():
        conn = appmod.mysql.connection._c
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        conn.commit()
        return rows


def as_user(client, uid):
    u = db("SELECT * FROM users WHERE id=?", (uid,))[0]
    with client.session_transaction() as s:
        s['user_id'], s['user_name'], s['role'], s['_csrf_token'] = u['id'], u['name'], u['role'], 'tok'


def post(client, url, data=None, **kw):
    data = dict(data or {})
    data.setdefault('csrf_token', 'tok')
    return client.post(url, data=data, **kw)


def flashes(client):
    with client.session_transaction() as s:
        return [m for _, m in s.get('_flashes', [])]


def current(student_id):
    return db("SELECT * FROM enrollments WHERE student_id=? AND is_current=1", (student_id,))


def edit_user(client, uid, **over):
    u = db("SELECT * FROM users WHERE id=?", (uid,))[0]
    data = dict(name=u['name'], email=u['email'], phone_number=u['phone_number'] or '', role=u['role'], status=u['status'])
    data.update(over)
    return post(client, f'/admin/users/edit/{uid}', data)


def upload(client, course_id, content=PDF, filename='prospectus.pdf', mimetype='application/pdf', **form):
    data = dict(form, csrf_token='tok', prospectus_file=(io.BytesIO(content), filename, mimetype))
    return client.post(f'/admin/courses/{course_id}/documents/upload', data=data, content_type='multipart/form-data')


@pytest.fixture()
def admin(env):
    c = env.test_client(); as_user(c, 1); return c


@pytest.fixture()
def student(env):
    c = env.test_client(); as_user(c, 3); return c


# ============ Scenario 1: assign ============
def test_s1_assign_program_and_student_sees_it_after_real_login(env, admin):
    r = edit_user(admin, 3, course_id='1')
    assert r.status_code == 302
    assert 'Student academic program successfully updated.' in flashes(admin)
    rows = current(3)
    assert len(rows) == 1 and rows[0]['course_id'] == 1 and rows[0]['enrollment_status'] == 'Enrolled'

    # genuine login (form + CSRF token scraped from the page), then profile
    c = env.test_client()
    page = c.get('/login').get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    r = c.post('/login', data={'email': 'juan@kll.test', 'password': 'secret123', 'csrf_token': token})
    assert r.status_code == 302
    html = c.get('/profile').get_data(as_text=True)
    assert 'MY ACADEMIC INFORMATION' in html.upper()
    assert 'BS Computer Science' in html and 'BSCS' in html and 'Enrolled' in html
    assert 'Juan Dela Cruz' in html and 'juan@kll.test' in html
    assert 'No Enrollment Data Yet' not in html


def test_s1_other_student_unaffected(env, admin):
    edit_user(admin, 3, course_id='1')
    assert current(4) == []
    c = env.test_client(); as_user(c, 4)
    assert 'No academic program has been assigned to your account yet. Please contact the Registrar' in c.get('/profile').get_data(as_text=True)


def test_dropdown_is_database_driven_and_active_only(env, admin):
    html = admin.get('/admin/users').get_data(as_text=True)
    assert 'BS Computer Science (BSCS)' in html and 'Bachelor of Public Administration (BPA)' in html
    assert 'Retired Program' not in html          # inactive program is not offered
    db("INSERT INTO courses (course_code,course_name,is_active) VALUES ('BSMA','BS Management Accounting',1)")
    assert 'BS Management Accounting (BSMA)' in admin.get('/admin/users').get_data(as_text=True)


def test_edit_modal_preselects_current_program(env, admin):
    edit_user(admin, 3, course_id='2')
    html = admin.get('/admin/users').get_data(as_text=True)
    assert re.search(r'<option value="2" selected>Bachelor of Public Administration \(BPA\)', html)


# ============ Scenario 2: change ============
def test_s2_change_program_keeps_history_and_single_current(env, admin):
    edit_user(admin, 3, course_id='1')
    edit_user(admin, 3, course_id='2')
    cur = current(3)
    assert len(cur) == 1 and cur[0]['course_id'] == 2
    allrows = db("SELECT course_id, enrollment_status, is_current FROM enrollments WHERE student_id=3 ORDER BY id")
    assert [(r['course_id'], r['enrollment_status']) for r in allrows] == [(1, 'Transferred'), (2, 'Enrolled')]
    c = env.test_client(); as_user(c, 3)
    html = c.get('/profile').get_data(as_text=True)
    assert 'Bachelor of Public Administration' in html and 'BS Computer Science' not in html


def test_unchanged_program_is_noop_and_preserved_when_field_absent(env, admin):
    edit_user(admin, 3, course_id='1')
    edit_user(admin, 3, course_id='1')                        # same value
    edit_user(admin, 3, name='Juan D. Cruz')                  # dropdown not submitted at all
    assert len(db("SELECT * FROM enrollments WHERE student_id=3")) == 1
    assert current(3)[0]['course_id'] == 1


def test_remove_assignment(env, admin):
    edit_user(admin, 3, course_id='1')
    edit_user(admin, 3, course_id='')                         # "No program assigned"
    assert current(3) == []
    row = db("SELECT enrollment_status FROM enrollments WHERE student_id=3")[0]
    assert row['enrollment_status'] == 'Withdrawn'
    # stand-alone remove route
    edit_user(admin, 3, course_id='2')
    r = post(admin, '/admin/students/3/program/remove', headers={'Referer': 'http://localhost/admin/enrollments'})
    assert r.status_code == 302 and current(3) == []


def test_reassign_after_removal_and_duplicate_submission(env, admin):
    for _ in range(3):
        post(admin, '/admin/students/3/program', {'course_id': '1'})
    assert len(db("SELECT * FROM enrollments WHERE student_id=3")) == 1
    post(admin, '/admin/students/3/program/remove')
    post(admin, '/admin/students/3/program', {'course_id': '1'})
    assert len(current(3)) == 1 and len(db("SELECT * FROM enrollments WHERE student_id=3")) == 2


def test_database_blocks_second_current_enrollment(env):
    db("INSERT INTO enrollments (student_id, course_id) VALUES (3, 1)")
    with pytest.raises(Exception):
        db("INSERT INTO enrollments (student_id, course_id) VALUES (3, 2)")   # is_current defaults to 1


def test_invalid_program_inputs_rejected(env, admin):
    for bad in ('999', 'abc', '1; DROP TABLE users', '-1', '4'):         # missing / junk / injection / inactive
        edit_user(admin, 3, course_id=bad)
        assert current(3) == [], bad
    assert db("SELECT COUNT(*) n FROM users")[0]['n'] == 4


def test_only_students_can_be_assigned(env, admin):
    edit_user(admin, 2, course_id='1')                                    # teacher
    assert db("SELECT * FROM enrollments") == []
    r = post(admin, '/admin/students/2/program', {'course_id': '1'})
    assert db("SELECT * FROM enrollments") == []
    assert any('only be assigned to student' in m for m in flashes(admin))


def test_failed_edit_rolls_back_user_changes(env, admin):
    edit_user(admin, 3, name='Changed Name', course_id='999')
    assert db("SELECT name FROM users WHERE id=3")[0]['name'] == 'Juan Dela Cruz'


def test_create_user_with_program_and_duplicate_email(env, admin):
    post(admin, '/admin/users/create', dict(name='New Kid', email='NEW@kll.test', password='abcdef', phone_number='',
                                            role='STUDENT', status='Active', course_id='3'))
    u = db("SELECT * FROM users WHERE email='new@kll.test'")[0]
    assert current(u['id'])[0]['course_id'] == 3
    post(admin, '/admin/users/create', dict(name='Dup', email='new@kll.test', password='abcdef', role='STUDENT',
                                            status='Active', course_id='1'))
    assert db("SELECT COUNT(*) n FROM users")[0]['n'] == 5
    assert db("SELECT COUNT(*) n FROM enrollments")[0]['n'] == 1


# ============ Scenario 3: enrollment management ============
def seed_enrollments(admin):
    for i in range(20):
        db("INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?, 'STUDENT')", (f'Bulk {i:02d}', f'bulk{i}@kll.test', 'x'))
    ids = [r['id'] for r in db("SELECT id FROM users WHERE email LIKE 'bulk%' ORDER BY id")]
    for n, uid in enumerate(ids):
        post(admin, f'/admin/students/{uid}/program', {'course_id': '1' if n < 12 else '2'})
    post(admin, '/admin/students/3/program', {'course_id': '1'})
    return ids


def test_s3_filter_search_counts_and_summary(env, admin):
    seed_enrollments(admin)
    html = admin.get('/admin/enrollments?program=1').get_data(as_text=True)
    assert 'of <strong>13</strong> record' in html and 'Bulk 12' not in html          # 12 bulk + Juan, BPA excluded
    assert 'Page' not in html or True
    html = admin.get('/admin/enrollments?program=2').get_data(as_text=True)
    assert 'of <strong>8</strong> record' in html
    html = admin.get('/admin/enrollments?q=juan').get_data(as_text=True)
    assert 'Juan Dela Cruz' in html and 'of <strong>1</strong>' in html
    html = admin.get('/admin/enrollments?q=bulk5%40').get_data(as_text=True)
    assert 'of <strong>1</strong>' in html
    assert 'of <strong>0</strong>' in admin.get('/admin/enrollments?q=%25').get_data(as_text=True)   # '%' is literal, not wildcard
    # summary cards = real DB numbers
    html = admin.get('/admin/enrollments').get_data(as_text=True)
    for value, label in ((22, 'Total Registered Students'), (21, 'Total Enrolled Students'),
                         (1, 'Students Without Assigned Programs'), (3, 'Total Active Academic Programs')):
        assert re.search(r'enroll-stat-value">%d</div><div class="enroll-stat-label">%s' % (value, label), html), label
    assert 'BSCS</strong> — 13 Students' in html and 'BSCRIM</strong> — 0 Students' in html


def test_s3_pagination_sort_and_status_filter(env, admin):
    seed_enrollments(admin)
    p1 = admin.get('/admin/enrollments?sort=name').get_data(as_text=True)
    p2 = admin.get('/admin/enrollments?sort=name&page=2').get_data(as_text=True)
    assert p1.count('class="btn-tbl-edit"') == 15 and p2.count('class="btn-tbl-edit"') == 6
    assert 'Bulk 00' in p1 and 'Bulk 00' not in p2 and 'page=2' in p1
    assert 'of <strong>21</strong>' in admin.get('/admin/enrollments?page=999').get_data(as_text=True)  # clamped
    post(admin, '/admin/students/3/program', {'course_id': '2'})            # Juan: history row appears
    assert 'of <strong>1</strong>' in admin.get('/admin/enrollments?status=Transferred').get_data(as_text=True)
    assert 'of <strong>22</strong>' in admin.get('/admin/enrollments?status=All').get_data(as_text=True)
    assert 'of <strong>21</strong>' in admin.get('/admin/enrollments?status=bogus&sort=bogus').get_data(as_text=True)


def test_enrollment_page_requires_admin(env, student):
    r = student.get('/admin/enrollments')
    assert r.status_code == 302 and '/admin/login' in r.headers['Location']


# ============ Scenario 4: upload ============
def test_s4_upload_prospectus(env, admin):
    r = upload(admin, 1, academic_year='2026-2027', document_title='BSCS Curriculum 2026')
    assert r.status_code == 302
    d = db("SELECT * FROM course_documents")[0]
    assert (d['course_id'], d['version'], d['is_current'], d['is_published'], d['file_type']) == (1, 1, 1, 1, 'pdf')
    assert d['uploaded_by'] == 1 and d['file_size'] == len(PDF) and d['academic_year'] == '2026-2027'
    assert d['original_filename'] == 'prospectus.pdf' and d['stored_filename'] != d['original_filename']
    stored = os.path.join(env.config['PROSPECTUS_FOLDER'], d['file_path'])
    assert open(stored, 'rb').read() == PDF
    assert 'static' not in os.path.relpath(stored, os.path.dirname(HERE)).split(os.sep)   # outside static/
    assert not os.path.isabs(d['file_path'])
    html = admin.get('/admin/courses').get_data(as_text=True)
    assert 'BSCS Curriculum 2026' in html and 'v1' in html


def test_docx_accepted_and_default_title(env, admin):
    upload(admin, 2, content=make_docx(), filename='cur.docx',
           mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    d = db("SELECT * FROM course_documents")[0]
    assert d['file_type'] == 'docx' and d['document_title'] == 'BPA Curriculum v1'


# ============ Scenario 5: student view / download ============
def test_s5_student_views_and_downloads_correct_file(env, admin):
    upload(admin, 1, content=PDF, document_title='BSCS Curriculum 2026')
    upload(admin, 2, content=PDF2, document_title='BPA Curriculum')
    edit_user(admin, 3, course_id='1')
    edit_user(admin, 4, course_id='2')
    c = env.test_client(); as_user(c, 3)
    html = c.get('/profile').get_data(as_text=True)
    assert 'BSCS Curriculum 2026' in html and 'VIEW PROSPECTUS' in html and 'DOWNLOAD PROSPECTUS' in html
    v = c.get('/student/prospectus/view')
    assert v.status_code == 200 and v.data == PDF and v.mimetype == 'application/pdf'
    assert v.headers['Content-Disposition'].startswith('inline') and v.headers['X-Content-Type-Options'] == 'nosniff'
    dl = c.get('/student/prospectus/download')
    assert dl.data == PDF and dl.headers['Content-Disposition'].startswith('attachment')
    assert 'BSCS_BSCS_Curriculum_2026_v1.pdf' in dl.headers['Content-Disposition']
    c2 = env.test_client(); as_user(c2, 4)
    assert c2.get('/student/prospectus/download').data == PDF2        # BPA student gets BPA, never BSCS


def test_student_without_document_or_program(env, admin):
    edit_user(admin, 3, course_id='1')
    c = env.test_client(); as_user(c, 3)
    assert 'not yet available. Please contact the Registrar' in c.get('/profile').get_data(as_text=True)
    assert c.get('/student/prospectus/download').status_code == 404
    c4 = env.test_client(); as_user(c4, 4)                            # no program at all
    assert c4.get('/student/prospectus/view').status_code == 404


# ============ Scenario 6: versions ============
def test_s6_new_version_archives_old_and_student_sees_latest(env, admin):
    upload(admin, 1, content=PDF, document_title='BSCS Curriculum 2024', academic_year='2024')
    upload(admin, 1, content=PDF2, document_title='BSCS Curriculum 2026', academic_year='2026')
    docs = db("SELECT version, is_current, is_published FROM course_documents ORDER BY version")
    assert [(d['version'], d['is_current']) for d in docs] == [(1, None), (2, 1)]
    assert len(os.listdir(os.path.join(env.config['PROSPECTUS_FOLDER'], 'course_1'))) == 2   # old file retained
    edit_user(admin, 3, course_id='1')
    c = env.test_client(); as_user(c, 3)
    assert c.get('/student/prospectus/download').data == PDF2
    # set the old one current again, then archive, then delete
    old = db("SELECT id FROM course_documents WHERE version=1")[0]['id']
    post(admin, f'/admin/courses/documents/{old}/publish')
    assert c.get('/student/prospectus/download').data == PDF
    cur = db("SELECT id FROM course_documents WHERE is_current=1")
    assert len(cur) == 1 and cur[0]['id'] == old
    post(admin, f'/admin/courses/documents/{old}/archive')
    assert c.get('/student/prospectus/download').status_code == 404
    post(admin, f'/admin/courses/documents/{old}/delete')
    assert len(db("SELECT * FROM course_documents")) == 1
    assert len(os.listdir(os.path.join(env.config['PROSPECTUS_FOLDER'], 'course_1'))) == 1


def test_draft_is_hidden_from_students_until_published(env, admin):
    upload(admin, 1, publish_mode='draft')
    edit_user(admin, 3, course_id='1')
    c = env.test_client(); as_user(c, 3)
    assert c.get('/student/prospectus/view').status_code == 404
    post(admin, f"/admin/courses/documents/{db('SELECT id FROM course_documents')[0]['id']}/publish")
    assert c.get('/student/prospectus/view').status_code == 200


def test_changing_student_program_changes_which_prospectus_they_get(env, admin):
    upload(admin, 1, content=PDF); upload(admin, 2, content=PDF2)
    edit_user(admin, 3, course_id='1')
    c = env.test_client(); as_user(c, 3)
    assert c.get('/student/prospectus/download').data == PDF
    edit_user(admin, 3, course_id='2')
    assert c.get('/student/prospectus/download').data == PDF2


# ============ Scenario 7: security ============
def test_unauthenticated_and_student_cannot_use_admin_endpoints(env, student):
    anon = env.test_client()
    with anon.session_transaction() as s:
        s['_csrf_token'] = 'tok'
    targets = [('/admin/enrollments', 'get'), ('/admin/users', 'get'), ('/admin/courses', 'get'),
               ('/admin/students/4/program', 'post'), ('/admin/students/4/program/remove', 'post'),
               ('/admin/courses/1/documents/upload', 'post'), ('/admin/users/edit/4', 'post')]
    for client in (anon, student):
        for url, method in targets:
            r = client.get(url) if method == 'get' else post(client, url, {'course_id': '1'})
            assert r.status_code == 302 and '/admin/login' in r.headers['Location'], (url, method)
    assert db("SELECT * FROM enrollments") == [] and db("SELECT * FROM course_documents") == []


def test_teacher_has_no_admin_rights(env):
    c = env.test_client(); as_user(c, 2)
    r = post(c, '/admin/students/3/program', {'course_id': '1'})
    assert r.status_code == 302 and '/admin/login' in r.headers['Location'] and current(3) == []
    assert c.get('/student/prospectus/view').status_code == 403
    assert c.get('/admin/enrollments').status_code == 302


def test_student_cannot_reach_other_students_or_documents(env, admin):
    upload(admin, 2, content=PDF2)
    doc_id = db("SELECT id FROM course_documents")[0]['id']
    edit_user(admin, 3, course_id='1'); edit_user(admin, 4, course_id='2')
    c = env.test_client(); as_user(c, 3)
    assert c.get(f'/admin/courses/documents/{doc_id}/download').status_code == 302       # admin route blocked
    assert c.get('/student/prospectus/download?doc_id=%d&student_id=4' % doc_id).status_code == 404   # params ignored
    assert c.get('/profile?student_id=4').get_data(as_text=True).count('Maria Santos') == 0
    assert post(c, f'/admin/courses/documents/{doc_id}/delete').status_code == 302
    assert len(db("SELECT * FROM course_documents")) == 1


@pytest.mark.parametrize('name,content,mime', [
    ('evil.exe', b'MZ' + b'\0' * 100, 'application/octet-stream'),
    ('evil.pdf.exe', PDF, 'application/pdf'),
    ('script.php', b'<?php ?>', 'text/plain'),
    ('fake.pdf', b'this is just text, not a pdf', 'application/pdf'),
    ('fake.docx', b'PK\x03\x04 not really a zip', 'application/octet-stream'),
    ('plain.pdf', PDF, 'text/html'),                       # MIME/extension mismatch
    ('empty.pdf', b'', 'application/pdf'),
    ('noext', PDF, 'application/pdf'),
])
def test_s7_invalid_uploads_rejected(env, admin, name, content, mime):
    r = upload(admin, 1, content=content, filename=name, mimetype=mime)
    assert r.status_code == 302
    assert db("SELECT * FROM course_documents") == []
    folder = os.path.join(env.config['PROSPECTUS_FOLDER'], 'course_1')
    assert not os.path.exists(folder) or os.listdir(folder) == []
    assert any(('Invalid' in m or 'not a valid' in m or 'empty' in m or 'choose' in m or 'match' in m) for m in flashes(admin)), flashes(admin)


def test_s7_oversized_upload(env, admin):
    env.config['PROSPECTUS_MAX_BYTES'] = 5000
    upload(admin, 1, content=PDF + b'x' * 6000)
    assert db("SELECT * FROM course_documents") == [] and any('too large' in m for m in flashes(admin))
    env.config['PROSPECTUS_MAX_BYTES'] = 10 * 1024 * 1024
    env.config['MAX_CONTENT_LENGTH'] = 20000                       # request-level cap -> 413 handler
    r = upload(admin, 1, content=PDF + b'x' * 50000)
    env.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
    assert r.status_code == 303 and db("SELECT * FROM course_documents") == []


def test_s7_invalid_course_and_doc_ids(env, admin):
    assert upload(admin, 9999).status_code == 302
    assert db("SELECT * FROM course_documents") == [] and any('not found' in m for m in flashes(admin))
    assert admin.get('/admin/courses/documents/9999/view').status_code == 404
    assert admin.get('/admin/courses/documents/9999/download').status_code == 404
    assert post(admin, '/admin/courses/documents/9999/publish').status_code == 302
    assert admin.get('/admin/courses/documents/abc/view').status_code == 404


def test_s7_missing_file_on_disk_returns_404(env, admin):
    upload(admin, 1); edit_user(admin, 3, course_id='1')
    d = db("SELECT * FROM course_documents")[0]
    os.remove(os.path.join(env.config['PROSPECTUS_FOLDER'], d['file_path']))
    c = env.test_client(); as_user(c, 3)
    assert c.get('/student/prospectus/download').status_code == 404
    assert admin.get(f"/admin/courses/documents/{d['id']}/view").status_code == 404


def test_s7_path_traversal_in_stored_path_is_not_served(env, admin, tmp_path):
    secret = tmp_path / 'secret.txt'; secret.write_text('TOP SECRET')
    upload(admin, 1); edit_user(admin, 3, course_id='1')
    for evil in ('../../secret.txt', str(secret), '..\\..\\secret.txt'):
        db("UPDATE course_documents SET file_path=?", (evil,))
        c = env.test_client(); as_user(c, 3)
        r = c.get('/student/prospectus/download')
        assert r.status_code == 404 and b'TOP SECRET' not in r.data, evil


def test_s7_csrf_is_enforced(env, admin):
    r = admin.post('/admin/students/3/program', data={'course_id': '1'})              # no token
    assert r.status_code == 303 and current(3) == []
    r = admin.post('/admin/students/3/program', data={'course_id': '1', 'csrf_token': 'wrong'})
    assert current(3) == []
    r = admin.post('/admin/students/3/program', data={'course_id': '1'}, headers={'X-CSRFToken': 'tok'})   # header form works
    assert len(current(3)) == 1
    anon = env.test_client()
    assert anon.post('/login', data={'email': 'a', 'password': 'b'}).status_code == 303          # even login needs a token
    assert 'csrf_token' in admin.get('/admin/users').get_data(as_text=True)


def test_csrf_redirect_never_leaves_site(env, admin):
    r = admin.post('/admin/students/3/program', data={'course_id': '1'}, headers={'Referer': 'http://evil.example/x'})
    assert r.status_code == 303 and 'evil.example' not in r.headers['Location']


# ============ course deletion guards ============
def test_course_with_enrollments_cannot_be_deleted_but_empty_one_cleans_files(env, admin):
    upload(admin, 1); edit_user(admin, 3, course_id='1')
    post(admin, '/admin/courses/delete/1')
    assert db("SELECT * FROM courses WHERE id=1") and len(current(3)) == 1
    assert any('cannot be deleted' in m for m in flashes(admin))
    upload(admin, 3)
    path = os.path.join(env.config['PROSPECTUS_FOLDER'], db("SELECT file_path FROM course_documents WHERE course_id=3")[0]['file_path'])
    assert os.path.exists(path)
    post(admin, '/admin/courses/delete/3')
    assert db("SELECT * FROM courses WHERE id=3") == [] and not os.path.exists(path)


def test_deleting_user_cascades_enrollment_rows_only_for_that_user(env, admin):
    edit_user(admin, 3, course_id='1'); edit_user(admin, 4, course_id='1')
    post(admin, '/admin/users/delete/3')
    assert current(3) == [] and len(current(4)) == 1


# ============ templates / regressions ============
def test_all_templates_compile_and_every_post_form_has_csrf(env):
    import glob
    root = os.path.join(os.path.dirname(HERE), 'templates')
    for f in glob.glob(root + '/**/*.html', recursive=True):
        rel = os.path.relpath(f, root)
        env.jinja_env.get_template(rel)
        src = open(f, encoding='utf-8').read()
        for m in re.finditer(r'<form\b[^>]*method\s*=\s*["\']post["\'][^>]*>', src, re.I):
            assert src[m.end():m.end() + 40].strip().startswith('{{ csrf_input() }}'), (rel, m.group(0)[:60])


def test_existing_admin_pages_still_render(env, admin):
    for url in ('/admin/users', '/admin/courses', '/admin/enrollments'):
        assert admin.get(url).status_code == 200, url
    c = env.test_client()
    assert c.get('/login').status_code == 200 and c.get('/register').status_code == 200


def test_registration_still_works_with_csrf(env):
    c = env.test_client()
    page = c.get('/register').get_data(as_text=True)
    tok = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    r = c.post('/register', data=dict(name='Reg Student', email='REG@kll.test', phone_number='0900', password='abc123',
                                      confirm_password='abc123', csrf_token=tok))
    assert r.status_code == 302 and db("SELECT role FROM users WHERE email='reg@kll.test'")[0]['role'] == 'STUDENT'
    assert 'No academic program has been assigned' in c.get('/profile').get_data(as_text=True)


def test_confirm_dialogs_survive_apostrophes_in_names(env, admin):
    db("INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?, 'STUDENT')", ("Sean O'Brien", 'sean@kll.test', 'x'))
    uid = db("SELECT id FROM users WHERE email='sean@kll.test'")[0]['id']
    post(admin, f'/admin/students/{uid}/program', {'course_id': '1'})
    for url in ('/admin/enrollments', '/admin/users'):
        html = admin.get(url).get_data(as_text=True)
        assert 'Sean O&#39;Brien' in html
        assert not re.search(r'onsubmit="return confirm\([^"]*O(\'|&#39;)Brien', html)
        assert re.search(r'data-confirm="[^"]*Sean O&#39;Brien', html)
