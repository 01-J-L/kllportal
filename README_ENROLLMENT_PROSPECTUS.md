# Student Enrollment & Prospectus Management — Technical Report

Everything below was added to the **existing** Flask + MySQL project. No tables were dropped, no module was rebuilt, and no new runtime dependency was introduced (`requirements.txt` is unchanged).

## 1. What was built
| Requirement | Where |
|---|---|
| Program dropdown in Create/Edit User (DB-driven, active programs only, searchable, remove option) | `templates/admin/users.html`, `admin_create_user`, `admin_edit_user` |
| Assign / change / remove program | `set_student_program()` + `/admin/students/<id>/program`, `/admin/students/<id>/program/remove` |
| Student Enrollment Management page (summary cards, per-program counts, program/name/email/status filters, sort, pagination) | `/admin/enrollments`, `templates/admin/enrollments.html`, sidebar link "Student Enrollment" |
| Student profile: **My Academic Information** + **My Curriculum / Prospectus** (View + Download) | `profile()`, `templates/portal/profile.html` |
| Prospectus upload / replace / archive / set-current / delete / view / download with versions | `Courses → PROSPECTUS` button, routes under `/admin/courses/...documents...`, `/student/prospectus/{view,download}` |
| CSRF on every POST | `csrf_protect()`; `{{ csrf_input() }}` added to all 42 POST forms |

## 2. Files
**Modified:** `app.py`, `config.py`, `static/css/style.css`, `static/js/main.js`, `templates/layout.html`, `templates/admin_layout.html`, `templates/admin/users.html`, `templates/admin/courses.html`, `templates/portal/profile.html`, and every template with a POST form (CSRF token only).
**New:** `migrations/enrollment_prospectus_upgrade.sql`, `templates/admin/enrollments.html`, `tests/` (`_shims.py`, `test_enrollment_prospectus.py`), `.gitignore`, `.env.example`, `requirements-dev.txt`, this file.
Uploaded prospectuses live in `protected_uploads/prospectus/course_<id>/<random>.pdf` (outside `static/`, git-ignored).

## 3. Database changes (migration is idempotent and non-destructive)
`enrollments` (existing table, reused) gains: `enrollment_status` (Enrolled / Transferred / Withdrawn), `is_current`, `academic_year`, `semester`, `assigned_by`, `updated_at`, plus
`UNIQUE (student_id, is_current)` and `INDEX (course_id, enrollment_status)`.
`is_current` is `1` for the student's current program and `NULL` for history; because unique indexes allow many NULLs, the **database itself** guarantees one current program per student while old rows are kept.
New table `course_documents` with all fields requested (+ FK to `courses` ON DELETE CASCADE, FK to `users` ON DELETE SET NULL, `UNIQUE (course_id, version)`, `UNIQUE (course_id, is_current)` = one official document per program).
**Legacy data:** if a student already has several enrollment rows, the newest becomes `Enrolled/current` and older ones become `Transferred` (kept, never deleted). `academic_year`/`semester` stay NULL until you decide to fill them — nothing is invented.

## 4. How it works
**Assignment.** One function, `set_student_program()`, is used by the user forms and the Enrollment page. In one transaction it locks the student row, validates (student role, program exists and is active), then: same program → no-op; different → old row becomes `Transferred`, new `Enrolled` row inserted; empty value → `Withdrawn`. The edit form only touches enrollment if the dropdown was submitted, so editing a name never wipes a program.
**Upload.** Admin-only → extension whitelist (pdf/docx/doc) → browser MIME must match → size ≤ 10 MB → file *content* sniffed (`%PDF-`, OLE header, or a real DOCX zip) → random stored name → file written, then DB row in a transaction (file removed again if the DB step fails). Publishing makes the new version current and **archives** the previous one; drafts are possible; "Set current" restores any old version. Nothing is deleted unless an admin clicks Delete.
**Download/View.** Students call `/student/prospectus/view|download` with **no ID**: the program is derived from their session and enrollment row, so there is nothing to tamper with. Files are streamed with `send_file` after a realpath check against the storage root; missing file/record → 404; `nosniff` + readable filename (`BSCS_BSCS_Curriculum_2026_v2.pdf`). DOC/DOCX are download-only; PDFs open inline.

## 5. Install / migrate an existing database
1. **Back up** the database (`mysqldump kll_portal_db > backup.sql`).
2. Copy the updated project over the old one (keep your `venv`).
3. Either run `mysql -u USER -p kll_portal_db < migrations/enrollment_prospectus_upgrade.sql`, **or** just start the app — it runs the same file on start-up (set `KLL_AUTO_MIGRATE=0` to disable). Watch the console for `[MIGRATION] ... OK`. If you see `INCOMPLETE`, run the SQL file manually and read the error. Re-running is safe.
4. `pip install -r requirements.txt`, then `python app.py`.
5. In production make sure `protected_uploads/` is writable and **backed up** with the database.

## 6. Testing
`pip install -r requirements-dev.txt && python -m pytest tests -v` → **46 tests, all passing**, covering test scenarios 1–7 of your brief (assign, change without duplicates, enrollment listing/filters/counts, upload, student view/download of the correct file, version replacement/archive, and security: role checks, CSRF, bad extensions/fake content/oversize, path traversal, missing files, invalid IDs, duplicate submissions). I also broke the code on purpose (CSRF off, traversal guard off, wrong-program leak, history overwrite, sniffing off) to confirm the tests fail when they should.

**Honest limits of that testing:** this sandbox had no MySQL server, so the tests run the app against an in-memory SQLite stand-in (`tests/_shims.py`). The new SQL is engine-neutral and the table layout is mirrored, but **`migrations/enrollment_prospectus_upgrade.sql` has not been executed on a real MySQL/MariaDB** — run it on a copy/staging database first. The CDN for Bootstrap was also blocked, so I verified the new JavaScript (searchable dropdown, role toggle, live search, no console errors) in headless Chromium but could not visually review the final styling; please eyeball the three new screens on your machine.

## 7. Things I noticed (not all changed)
* **Secrets are hard-coded** in `config.py` (DB password, Gmail app password) and `credential.txt`. Rotate them and move to environment variables (`.env.example` provided). I did not alter them to avoid breaking your setup.
* Login still accepts a plain-text stored password (`user['password_hash'] == password`). Recommend removing once all passwords are hashed.
* `app.run(debug=True)` — never run that in production.
* Behaviour changes I made on purpose: a program with enrollment records can no longer be deleted (mark it Inactive instead) because deletion would cascade-delete students' enrollment history; user create/edit now validates role, status, e-mail format/uniqueness and lowercases e-mails; the profile no longer shows a made-up fallback phone number; a `NULL` course description no longer crashes the Courses page.
* Other pre-existing templates still use inline `onsubmit="return confirm('{{ name }}')"`, which breaks (and skips the confirmation) when a name contains an apostrophe. I fixed it only for the screens I touched; the same `data-confirm` pattern can be applied elsewhere.
* ADMIN accounts can create/edit SUPER_ADMIN accounts (existing behaviour).
* Out of scope but worth a later look: students cannot yet *request* a program change (admins only, as required); academic year/semester are stored but not yet exposed in the UI.
