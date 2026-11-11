import os
import csv
import io
import re
import unicodedata
import psycopg
from psycopg.rows import dict_row
import secrets
import hmac
import hashlib
import base64
from cryptography.fernet import Fernet, InvalidToken
from datetime import datetime
from functools import wraps
from pathlib import Path
from contextlib import contextmanager
from dotenv import load_dotenv


from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, send_from_directory, send_file, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_wtf.csrf import CSRFProtect, CSRFError
from vercel.blob import sync as vercel_blob

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')
DATABASE_URL = os.environ.get("DATABASE_URL", "")
UPLOAD_DIR = BASE_DIR / "uploads"
USE_VERCEL_BLOB = bool(os.environ.get("BLOB_STORE_ID") or os.environ.get("BLOB_READ_WRITE_TOKEN"))
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_UPLOAD_BYTES = 4 * 1024 * 1024

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "ganti-secret-key-ini-sebelum-deploy"),
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=bool(os.environ.get("VERCEL")),
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_PATH="/",
    SESSION_COOKIE_NAME="jurnify_session",
    # Form login dapat dibuka lama; token CSRF tetap memiliki masa berlaku terbatas.
    WTF_CSRF_TIME_LIMIT=8 * 60 * 60,
    WTF_CSRF_SSL_STRICT=True,
)
csrf = CSRFProtect(app)

@app.after_request
def prevent_dynamic_page_caching(response):
    # Jangan biarkan halaman form ber-CSRF token disimpan cache browser/CDN.
    # Ini penting pada deployment serverless agar token lama tidak dipakai ulang.
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "private, no-store, no-cache, max-age=0, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Vary"] = "Cookie"
    return response

@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    # Token/session yang hilang atau kedaluwarsa diarahkan ke form baru,
    # bukan menampilkan halaman 400 mentah kepada pengguna.
    app.logger.warning("CSRF ditolak pada %s: %s", request.path, error.description)
    if request.endpoint == "login" or request.path.rstrip("/") == "/login":
        flash("Sesi login kedaluwarsa atau cookie tidak terbaca. Silakan masuk kembali.", "warning")
        return redirect(url_for("login"), code=303)
    return render_template("error.html", code=400,
                           message="Sesi formulir kedaluwarsa. Muat ulang halaman lalu coba lagi."), 400

if not os.environ.get("VERCEL"):
    UPLOAD_DIR.mkdir(exist_ok=True)

def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def class_password(username):
    """Password akun kelas yang dapat ditampilkan kembali tanpa menyimpan plaintext."""
    secret = app.config["SECRET_KEY"].encode("utf-8")
    digest = hmac.new(secret, ("jurnal-pkl-class:" + username).encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:14]

def _class_password_cipher():
    key = base64.urlsafe_b64encode(hashlib.sha256(app.config["SECRET_KEY"].encode("utf-8")).digest())
    return Fernet(key)

def encrypt_class_password(password):
    return _class_password_cipher().encrypt(password.encode("utf-8")).decode("ascii")

def decrypt_class_password(value):
    if not value:
        return ""
    try:
        return _class_password_cipher().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return ""

@contextmanager
def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL belum diatur. Isi connection string Neon di file .env.")
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, sslmode="require")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','student')),
            full_name TEXT NOT NULL,
            supervisor TEXT NOT NULL DEFAULT '',
            workplace TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """)
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS class_password_encrypted TEXT")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id BIGSERIAL PRIMARY KEY,
            class_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            full_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(class_user_id, full_name)
        )
        """)
        conn.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS workplace TEXT NOT NULL DEFAULT ''")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS journals (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            student_id BIGINT REFERENCES students(id) ON DELETE CASCADE,
            attendance_date DATE NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('Hadir','Izin','Sakit')),
            note TEXT NOT NULL DEFAULT '',
            photo_filename TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            verification_status TEXT NOT NULL DEFAULT 'Menunggu'
                CHECK(verification_status IN ('Menunggu','Disetujui','Ditolak')),
            verified_by BIGINT REFERENCES users(id),
            verified_at TEXT,
            verification_note TEXT
        )
        """)
        conn.execute("ALTER TABLE journals ADD COLUMN IF NOT EXISTS activity TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE journals ADD COLUMN IF NOT EXISTS work_process TEXT NOT NULL DEFAULT ''")
        # Upgrade database lama: jurnal kini terikat ke siswa yang dipilih dalam akun kelas.
        conn.execute("ALTER TABLE journals ADD COLUMN IF NOT EXISTS student_id BIGINT REFERENCES students(id) ON DELETE CASCADE")
        conn.execute("ALTER TABLE journals DROP CONSTRAINT IF EXISTS journals_user_id_attendance_date_key")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS journals_class_student_date_unique ON journals(user_id, student_id, attendance_date) WHERE student_id IS NOT NULL")
        admin = conn.execute("SELECT id FROM users WHERE username=%s", ("admin",)).fetchone()
        if not admin:
            if os.environ.get("VERCEL") and not os.environ.get("ADMIN_PASSWORD"):
                raise RuntimeError("ADMIN_PASSWORD wajib diatur sebelum membuat akun admin pertama.")
            conn.execute(
                """INSERT INTO users
                (username,password_hash,role,full_name,supervisor,workplace,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                ("admin", generate_password_hash(os.environ.get("ADMIN_PASSWORD", "AdminPKL123!")),
                 "admin", "Administrator", "", "", now())
            )

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get("role") != "admin":
            abort(403)
        return fn(*args, **kwargs)
    return wrapper

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def save_photo(file):
    if not file or not file.filename:
        raise ValueError("Foto bukti wajib diunggah.")
    if not allowed_file(file.filename):
        raise ValueError("Format foto harus JPG, JPEG, PNG, atau WEBP.")
    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = f"{secrets.token_hex(16)}.{ext}"
    if USE_VERCEL_BLOB:
        payload = file.read()
        uploaded = vercel_blob.put_blob(
            f"jurnify/{filename}", payload, access="private",
            content_type=file.mimetype or f"image/{ext}"
        )
        return uploaded.pathname
    file.save(UPLOAD_DIR / filename)
    return filename

def delete_photo(filename):
    if not filename:
        return
    if USE_VERCEL_BLOB:
        try:
            vercel_blob.delete_blob(filename)
        except Exception:
            app.logger.exception("Gagal menghapus foto dari Vercel Blob: %s", filename)
    else:
        (UPLOAD_DIR / filename).unlink(missing_ok=True)

def load_photo(filename):
    if USE_VERCEL_BLOB:
        result = vercel_blob.get_blob(filename, access="private")
        return result.body, result.metadata.content_type or "application/octet-stream"
    path = UPLOAD_DIR / Path(filename).name
    if not path.is_file():
        raise FileNotFoundError(filename)
    return path.read_bytes(), "application/octet-stream"

@app.context_processor
def inject_user():
    if "user_id" not in session:
        return {"current_user": None}
    with get_db() as conn:
        user = conn.execute("SELECT id,username,role,full_name FROM users WHERE id=%s",
                            (session["user_id"],)).fetchone()
    return {"current_user": user}

@app.route("/")
@login_required
def dashboard():
    with get_db() as conn:
        if session["role"] == "admin":
            stats = {
                "total": conn.execute("SELECT COUNT(*) AS n FROM journals").fetchone()["n"],
                "waiting": conn.execute("SELECT COUNT(*) AS n FROM journals WHERE verification_status='Menunggu'").fetchone()["n"],
                "approved": conn.execute("SELECT COUNT(*) AS n FROM journals WHERE verification_status='Disetujui'").fetchone()["n"],
                "rejected": conn.execute("SELECT COUNT(*) AS n FROM journals WHERE verification_status='Ditolak'").fetchone()["n"],
            }
            recent = conn.execute("""
                SELECT j.*,s.full_name,u.full_name AS class_name FROM journals j JOIN users u ON u.id=j.user_id
                LEFT JOIN students s ON s.id=j.student_id
                ORDER BY j.submitted_at DESC LIMIT 8
            """).fetchall()
            class_accounts = conn.execute("""
                SELECT u.id,u.full_name AS class_name,u.username,u.class_password_encrypted,COUNT(s.id) AS student_count
                FROM users u LEFT JOIN students s ON s.class_user_id=u.id
                WHERE u.role='student' AND EXISTS (SELECT 1 FROM students sx WHERE sx.class_user_id=u.id)
                GROUP BY u.id,u.full_name,u.username ORDER BY u.full_name
            """).fetchall()
            class_accounts = [dict(row) for row in class_accounts]
            return render_template("dashboard_admin.html", stats=stats, recent=recent, class_accounts=class_accounts)
        profile = conn.execute("SELECT * FROM users WHERE id=%s", (session["user_id"],)).fetchone()
        class_students = conn.execute("SELECT id,full_name,workplace FROM students WHERE class_user_id=%s ORDER BY full_name", (session["user_id"],)).fetchall()
        selected_student = request.args.get("student_id", "").strip()
        selected_student_name = None
        valid_student_ids = {str(student["id"]) for student in class_students}
        if selected_student not in valid_student_ids:
            selected_student = ""
        if selected_student:
            selected_student_name = next((student["full_name"] for student in class_students if str(student["id"]) == selected_student), None)
            journals = conn.execute("""
                SELECT j.*, s.full_name AS student_name
                FROM journals j JOIN students s ON s.id=j.student_id
                WHERE j.user_id=%s AND j.student_id=%s
                ORDER BY j.submitted_at DESC
            """, (session["user_id"], int(selected_student))).fetchall()
        else:
            journals = conn.execute("""
                SELECT j.*, s.full_name AS student_name
                FROM journals j LEFT JOIN students s ON s.id=j.student_id
                WHERE j.user_id=%s ORDER BY j.submitted_at DESC
            """, (session["user_id"],)).fetchall()
        return render_template("dashboard_student.html", journals=journals, profile=profile,
                               class_students=class_students, selected_student=selected_student,
                               selected_student_name=selected_student_name)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE username=%s", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            return redirect(url_for("dashboard"))
        flash("Username atau password salah.", "danger")
    return render_template("login.html")

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("Anda telah keluar.", "success")
    return redirect(url_for("login"))

@app.route("/jurnal/tambah", methods=["GET", "POST"])
@login_required
def add_journal():
    if session["role"] != "student":
        abort(403)
    with get_db() as conn:
        profile = conn.execute("SELECT * FROM users WHERE id=%s", (session["user_id"],)).fetchone()
        class_students = conn.execute("SELECT id,full_name,workplace FROM students WHERE class_user_id=%s ORDER BY full_name", (session["user_id"],)).fetchall()
    if not class_students:
        flash("Daftar siswa kelas belum diimpor. Hubungi guru/admin.", "danger")
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        date = request.form.get("attendance_date", "")
        status = request.form.get("status", "")
        note = request.form.get("note", "").strip()
        activity = request.form.get("activity", "").strip() or note
        student_id = request.form.get("student_id", "")
        workplace = request.form.get("workplace", "").strip()
        photo = request.files.get("photo")
        filename = None
        try:
            datetime.strptime(date, "%Y-%m-%d")
            if status not in {"Hadir", "Izin", "Sakit"}:
                raise ValueError("Status kehadiran tidak valid.")
            if not student_id.isdigit() or int(student_id) not in {int(s["id"]) for s in class_students}:
                raise ValueError("Pilih nama siswa yang terdaftar di kelas ini.")
            filename = save_photo(photo)
            with get_db() as conn:
                selected_student = conn.execute(
                    "SELECT id,workplace FROM students WHERE id=%s AND class_user_id=%s",
                    (int(student_id), session["user_id"])
                ).fetchone()
                if not selected_student:
                    raise ValueError("Siswa tidak ditemukan pada akun kelas ini.")
                if not (selected_student["workplace"] or "").strip():
                    if not workplace:
                        raise ValueError("Nama tempat PKL wajib diisi saat pertama kali mengirim jurnal.")
                    conn.execute(
                        "UPDATE students SET workplace=%s WHERE id=%s AND (workplace IS NULL OR workplace='')",
                        (workplace, int(student_id))
                    )
                conn.execute("""
                    INSERT INTO journals
                    (user_id,student_id,attendance_date,status,note,activity,photo_filename,submitted_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """, (session["user_id"], int(student_id), date, status, note, activity, filename, now()))
            flash("Jurnal berhasil dikirim dan menunggu verifikasi guru/admin.", "success")
            return redirect(url_for("dashboard"))
        except psycopg.errors.UniqueViolation:
            if filename:
                delete_photo(filename)
            flash("Siswa tersebut sudah mengirim jurnal untuk tanggal itu.", "danger")
        except ValueError as exc:
            if filename:
                delete_photo(filename)
            flash(str(exc), "danger")
    return render_template("journal_form.html", profile=profile, class_students=class_students,
                           today=datetime.now().date().isoformat())

def _class_username(class_name, conn):
    normalized = unicodedata.normalize("NFKD", class_name).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-") or "kelas"
    base = f"kelas-{slug}"
    candidate = base
    suffix = 2
    while conn.execute("SELECT id FROM users WHERE username=%s", (candidate,)).fetchone():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def parse_student_csv(file_storage):
    raw = file_storage.read()
    if not raw:
        raise ValueError("File CSV kosong.")
    decoded = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            decoded = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError("Encoding CSV tidak dapat dibaca. Simpan sebagai CSV UTF-8.")
    rows = list(csv.reader(io.StringIO(decoded)))
    rows = [[cell.strip() for cell in row] for row in rows]
    rows = [row for row in rows if any(cell for cell in row)]
    if not rows:
        raise ValueError("File CSV tidak berisi data.")

    # Format sederhana: kelas,nama_siswa (bisa memuat beberapa kelas).
    header_idx = None
    for i, row in enumerate(rows[:10]):
        normalized = [re.sub(r"[\s_-]+", "", c.lower()) for c in row]
        if "kelas" in normalized and any(x in normalized for x in ("namasiswa", "nama")):
            header_idx = (i, normalized.index("kelas"), normalized.index("namasiswa") if "namasiswa" in normalized else normalized.index("nama"))
            break
    records = []
    if header_idx:
        i, class_col, name_col = header_idx
        for row in rows[i+1:]:
            if max(class_col, name_col) < len(row):
                class_name, student_name = row[class_col].strip(), row[name_col].strip()
                if class_name and student_name:
                    records.append((class_name, student_name))
    else:
        # Format daftar hadir sekolah: kelas pada baris metadata, nama siswa pada kolom NAMA SISWA.
        detected_class = ""
        for row in rows:
            for idx, cell in enumerate(row):
                if re.sub(r"\s+", " ", cell.upper()).strip(" :") == "KELAS":
                    detected_class = next((x for x in row[idx+1:] if x and x != ":"), "")
                    break
            if detected_class:
                break
        name_col = None
        data_start = None
        for i, row in enumerate(rows):
            for idx, cell in enumerate(row):
                if re.sub(r"\s+", " ", cell.upper()).strip() == "NAMA SISWA":
                    name_col, data_start = idx, i + 1
                    break
            if name_col is not None:
                break
        if not detected_class or name_col is None:
            raise ValueError("Format CSV tidak dikenali. Pastikan ada kolom kelas dan nama siswa, atau gunakan CSV daftar hadir sekolah.")
        footer_markers = {
            "JUMLAH", "TANDA TANGAN GURU", "JURNAL KELAS", "KEPALA SEKOLAH",
            "WAKA KURIKULUM", "PIKET GURU", "PIKET SISWA"
        }
        for row in rows[data_start:]:
            if name_col < len(row):
                name = row[name_col].strip()
                normalized_name = re.sub(r"\s+", " ", name.upper()).strip(" :")
                # Bagian daftar siswa berakhir pada baris JUMLAH atau bagian tanda tangan.
                if normalized_name in footer_markers:
                    break
                # Abaikan baris judul/nomor/kosong; nama pada template biasanya berada di baris bernomor.
                if name and normalized_name not in {"NAMA SISWA", "URUT"} and not name.isdigit():
                    records.append((detected_class, name))

    cleaned = []
    seen = set()
    for class_name, student_name in records:
        class_name = re.sub(r"\s+", " ", class_name).strip()
        student_name = re.sub(r"\s+", " ", student_name).strip()
        key = (class_name.casefold(), student_name.casefold())
        if class_name and student_name and key not in seen:
            cleaned.append((class_name, student_name))
            seen.add(key)
    if not cleaned:
        raise ValueError("Tidak ditemukan data nama siswa yang valid.")
    return cleaned


@app.route("/admin/siswa", methods=["GET", "POST"])
@login_required
@admin_required
def students():
    if request.method == "POST":
        upload = request.files.get("csv_file")
        try:
            if not upload or not upload.filename.lower().endswith(".csv"):
                raise ValueError("Pilih file CSV terlebih dahulu.")
            records = parse_student_csv(upload)
            created_classes = []
            added = 0
            skipped = 0
            credentials = []
            with get_db() as conn:
                for class_name in sorted({r[0] for r in records}, key=str.casefold):
                    class_user = conn.execute("SELECT id,username FROM users WHERE role='student' AND lower(full_name)=lower(%s)", (class_name,)).fetchone()
                    if not class_user:
                        username = _class_username(class_name, conn)
                        initial_password = class_password(username)
                        class_user = conn.execute("""
                            INSERT INTO users(username,password_hash,role,full_name,supervisor,workplace,created_at,class_password_encrypted)
                            VALUES(%s,%s,'student',%s,'','',%s,%s) RETURNING id,username
                        """, (username, generate_password_hash(initial_password), class_name, now(),
                              encrypt_class_password(initial_password))).fetchone()
                        created_classes.append(class_name)
                    for _, student_name in [r for r in records if r[0].casefold() == class_name.casefold()]:
                        exists = conn.execute("SELECT id FROM students WHERE class_user_id=%s AND lower(full_name)=lower(%s)", (class_user["id"], student_name)).fetchone()
                        if exists:
                            skipped += 1
                            continue
                        conn.execute("INSERT INTO students(class_user_id,full_name,created_at) VALUES(%s,%s,%s)",
                                     (class_user["id"], student_name, now()))
                        added += 1
            flash(f"Impor selesai: {added} siswa ditambahkan, {skipped} duplikat dilewati, {len(created_classes)} akun kelas dibuat.", "success")
            return redirect(url_for("students"))
        except ValueError as exc:
            flash(str(exc), "danger")
    with get_db() as conn:
        classes = conn.execute("""
            SELECT u.id,u.full_name AS class_name,u.username,u.class_password_encrypted,COUNT(s.id) AS student_count
            FROM users u LEFT JOIN students s ON s.class_user_id=u.id
            WHERE u.role='student' AND EXISTS (SELECT 1 FROM students sx WHERE sx.class_user_id=u.id)
            GROUP BY u.id,u.full_name,u.username ORDER BY u.full_name
        """).fetchall()
        selected_class = request.args.get("class_id", "")
        data = conn.execute("""
            SELECT s.id,s.full_name,s.workplace,u.full_name AS class_name,u.username,u.id AS class_user_id
            FROM students s JOIN users u ON u.id=s.class_user_id
            WHERE (%s = '' OR u.id::text = %s)
            ORDER BY u.full_name,s.full_name
        """, (selected_class, selected_class)).fetchall()
        journal_logs = conn.execute("""
            SELECT j.student_id,j.attendance_date,j.status,j.submitted_at,j.verification_status
            FROM journals j
            JOIN students s ON s.id=j.student_id
            JOIN users u ON u.id=s.class_user_id
            WHERE (%s = '' OR u.id::text = %s)
            ORDER BY j.submitted_at DESC
        """, (selected_class, selected_class)).fetchall()
    logs_by_student = {}
    for log in journal_logs:
        logs_by_student.setdefault(log["student_id"], []).append(log)
    for student in data:
        student["journal_logs"] = logs_by_student.get(student["id"], [])
    for account in classes:
        account["visible_password"] = decrypt_class_password(account.get("class_password_encrypted"))
    return render_template("students.html", students=data, classes=classes,
                           selected_class=selected_class)

@app.route("/admin/akun-kelas/<int:class_id>/password", methods=["POST"])
@login_required
@admin_required
def update_class_password(class_id):
    new_password = request.form.get("new_password", "")
    if len(new_password) < 8:
        flash("Password baru minimal 8 karakter.", "danger")
        return redirect(url_for("students"))
    with get_db() as conn:
        account = conn.execute(
            "SELECT id,full_name FROM users WHERE id=%s AND role='student'",
            (class_id,)
        ).fetchone()
        if not account:
            abort(404)
        conn.execute(
            "UPDATE users SET password_hash=%s,class_password_encrypted=%s WHERE id=%s",
            (generate_password_hash(new_password), encrypt_class_password(new_password), class_id)
        )
    flash(f"Password akun kelas {account['full_name']} berhasil diperbarui.", "success")
    return redirect(url_for("students"))

@app.route("/admin/akun-kelas/<int:class_id>/username", methods=["POST"])
@login_required
@admin_required
def update_class_username(class_id):
    new_username = request.form.get("username", "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,39}", new_username):
        flash("Username harus 3–40 karakter, diawali huruf/angka, dan hanya berisi huruf kecil, angka, titik, garis bawah, atau tanda hubung.", "danger")
        return redirect(url_for("students"))
    with get_db() as conn:
        account = conn.execute(
            "SELECT id,full_name FROM users WHERE id=%s AND role='student'",
            (class_id,)
        ).fetchone()
        if not account:
            abort(404)
        duplicate = conn.execute(
            "SELECT id FROM users WHERE lower(username)=lower(%s) AND id<>%s",
            (new_username, class_id)
        ).fetchone()
        if duplicate:
            flash("Username tersebut sudah digunakan akun lain.", "danger")
            return redirect(url_for("students"))
        conn.execute("UPDATE users SET username=%s WHERE id=%s", (new_username, class_id))
    flash(f"Username akun kelas {account['full_name']} berhasil diubah menjadi {new_username}.", "success")
    return redirect(url_for("students"))

@app.route("/admin/akun-kelas/<int:class_id>/nama", methods=["POST"])
@login_required
@admin_required
def update_class_name(class_id):
    new_name = " ".join(request.form.get("class_name", "").split())
    if not new_name or len(new_name) > 100:
        flash("Nama kelas wajib diisi dan maksimal 100 karakter.", "danger")
        return redirect(url_for("students"))
    with get_db() as conn:
        account = conn.execute("SELECT id FROM users WHERE id=%s AND role='student'", (class_id,)).fetchone()
        if not account:
            abort(404)
        duplicate = conn.execute(
            "SELECT id FROM users WHERE role='student' AND lower(full_name)=lower(%s) AND id<>%s",
            (new_name, class_id)
        ).fetchone()
        if duplicate:
            flash("Nama kelas tersebut sudah digunakan akun kelas lain.", "danger")
            return redirect(url_for("students"))
        conn.execute("UPDATE users SET full_name=%s WHERE id=%s", (new_name, class_id))
    flash(f"Nama kelas berhasil diubah menjadi {new_name}.", "success")
    return redirect(url_for("students"))

@app.route("/admin/siswa/<int:student_id>/hapus", methods=["POST"])
@login_required
@admin_required
def delete_student(student_id):
    with get_db() as conn:
        student = conn.execute("SELECT full_name FROM students WHERE id=%s", (student_id,)).fetchone()
        if not student:
            abort(404)
        conn.execute("DELETE FROM students WHERE id=%s", (student_id,))
    flash(f"Data siswa {student['full_name']} dan seluruh jurnal terkait telah dihapus.", "success")
    return redirect(url_for("students"))

@app.route("/admin/akun-kelas/hapus-banyak", methods=["POST"])
@login_required
@admin_required
def delete_class_accounts_bulk():
    raw_ids = request.form.getlist("class_ids")
    class_ids = sorted({int(value) for value in raw_ids if value.isdigit()})
    if not class_ids:
        flash("Pilih minimal satu akun kelas yang ingin dihapus.", "warning")
        return redirect(url_for("students"))
    with get_db() as conn:
        accounts = conn.execute(
            "SELECT id,full_name FROM users WHERE role='student' AND id = ANY(%s)",
            (class_ids,)
        ).fetchall()
        if accounts:
            conn.execute("DELETE FROM users WHERE role='student' AND id = ANY(%s)", (class_ids,))
    deleted_names = [account["full_name"] for account in accounts]
    flash(f"{len(deleted_names)} akun kelas dihapus: {', '.join(deleted_names)}. Seluruh siswa dan jurnal terkait ikut dihapus permanen.", "success")
    return redirect(url_for("students"))

@app.route("/admin/akun-kelas/<int:class_id>/hapus", methods=["POST"])
@login_required
@admin_required
def delete_class_account(class_id):
    with get_db() as conn:
        account = conn.execute("""
            SELECT full_name FROM users WHERE id=%s AND role='student'
        """, (class_id,)).fetchone()
        if not account:
            abort(404)
        conn.execute("DELETE FROM users WHERE id=%s AND role='student'", (class_id,))
    flash(f"Akun kelas {account['full_name']} beserta daftar siswa dan seluruh jurnal terkait telah dihapus.", "success")
    return redirect(url_for("students"))

@app.route("/admin/verifikasi")
@login_required
@admin_required
def verification_queue():
    with get_db() as conn:
        data = conn.execute("""
            SELECT j.*,s.full_name,u.full_name AS class_name,u.supervisor,
                   COALESCE(NULLIF(s.workplace,''),u.workplace,'') AS workplace
            FROM journals j JOIN users u ON u.id=j.user_id
            LEFT JOIN students s ON s.id=j.student_id
            ORDER BY CASE j.verification_status WHEN 'Menunggu' THEN 0 ELSE 1 END,
                     j.submitted_at DESC
        """).fetchall()
    return render_template("verification.html", journals=data)

@app.route("/admin/verifikasi/<int:journal_id>", methods=["POST"])
@login_required
@admin_required
def verify_journal(journal_id):
    action = request.form.get("action")
    note = request.form.get("verification_note", "").strip()
    if action not in {"Disetujui", "Ditolak"}:
        abort(400)
    with get_db() as conn:
        row = conn.execute("SELECT id FROM journals WHERE id=%s", (journal_id,)).fetchone()
        if not row:
            abort(404)
        conn.execute("""
            UPDATE journals SET verification_status=%s, verified_by=%s, verified_at=%s,
                verification_note=%s WHERE id=%s
        """, (action, session["user_id"], now(), note, journal_id))
    flash(f"Jurnal telah {action.lower()}.", "success")
    return redirect(url_for("verification_queue"))

@app.route("/admin/rekap")
@login_required
@admin_required
def report():
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    class_id = request.args.get("class_id", "")
    student_id = request.args.get("student_id", "")
    params = []
    query = """
        SELECT j.*,s.full_name,s.workplace,u.full_name AS class_name,u.username,u.supervisor
        FROM journals j JOIN users u ON u.id=j.user_id
        LEFT JOIN students s ON s.id=j.student_id WHERE 1=1
    """
    if class_id:
        query += " AND u.id = %s"
        params.append(class_id)
    if start:
        query += " AND j.attendance_date >= %s"
        params.append(start)
    if end:
        query += " AND j.attendance_date <= %s"
        params.append(end)
    if student_id:
        query += " AND j.student_id = %s"
        params.append(student_id)
    query += " ORDER BY j.submitted_at DESC,u.full_name,s.full_name"
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall() if student_id else []
        classes = conn.execute("""
            SELECT id,full_name FROM users
            WHERE role='student' AND EXISTS (SELECT 1 FROM students WHERE class_user_id=users.id)
            ORDER BY full_name
        """).fetchall()
        student_list = conn.execute("""
            SELECT s.id,s.full_name,u.full_name AS class_name,s.class_user_id
            FROM students s JOIN users u ON u.id=s.class_user_id
            WHERE (%s = '' OR u.id::text = %s)
            ORDER BY u.full_name,s.full_name
        """, (class_id, class_id)).fetchall()
        summary_query = """
            SELECT s.id AS student_id,s.full_name,s.workplace,u.full_name AS class_name,
                   COUNT(j.id) FILTER (WHERE j.status='Izin') AS izin,
                   COUNT(j.id) FILTER (WHERE j.status='Sakit') AS sakit,
                   COUNT(j.id) FILTER (WHERE j.status='Hadir') AS hadir,
                   COUNT(j.id) AS total
            FROM students s
            JOIN users u ON u.id=s.class_user_id
            LEFT JOIN journals j ON j.student_id=s.id
        """
        summary_params = []
        if start:
            summary_query += " AND j.attendance_date >= %s"
            summary_params.append(start)
        if end:
            summary_query += " AND j.attendance_date <= %s"
            summary_params.append(end)
        summary_query += " WHERE (%s = '' OR u.id::text = %s) GROUP BY s.id,s.full_name,u.full_name ORDER BY s.full_name"
        summary_params.extend([class_id, class_id])
        summary = conn.execute(summary_query, summary_params).fetchall()
    return render_template("report.html", journals=rows, summary=summary, students=student_list,
                           classes=classes, class_id=class_id, start=start, end=end,
                           student_id=student_id)

@app.route("/admin/rekap.pdf")
@login_required
@admin_required
def report_pdf():
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, KeepTogether
    from reportlab.lib.utils import ImageReader
    from xml.sax.saxutils import escape

    class_id = request.args.get("class_id", "")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    student_id = request.args.get("student_id", "")
    query = """
        SELECT j.*,s.full_name,s.workplace,u.full_name AS class_name,u.supervisor
        FROM journals j JOIN users u ON u.id=j.user_id
        LEFT JOIN students s ON s.id=j.student_id WHERE 1=1
    """
    params = []
    if class_id:
        query += " AND u.id=%s"; params.append(class_id)
    if start:
        query += " AND j.attendance_date >= %s"; params.append(start)
    if end:
        query += " AND j.attendance_date <= %s"; params.append(end)
    if student_id:
        query += " AND j.student_id=%s"; params.append(student_id)
    query += " ORDER BY j.attendance_date ASC,s.full_name"
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
        profile = None
        if student_id:
            profile = conn.execute("""
                SELECT s.full_name,s.workplace,u.full_name AS class_name,u.supervisor
                FROM students s JOIN users u ON u.id=s.class_user_id WHERE s.id=%s
            """, (student_id,)).fetchone()
        elif class_id:
            profile = conn.execute("""
                SELECT u.full_name AS class_name,u.supervisor,'' AS workplace
                FROM users u WHERE u.id=%s
            """, (class_id,)).fetchone()
        summary_query = """
            SELECT s.full_name,s.workplace,
                   COUNT(j.id) FILTER (WHERE j.status='Izin') AS izin,
                   COUNT(j.id) FILTER (WHERE j.status='Sakit') AS sakit,
                   COUNT(j.id) FILTER (WHERE j.status='Hadir') AS hadir,
                   COUNT(j.id) AS total
            FROM students s JOIN users u ON u.id=s.class_user_id
            LEFT JOIN journals j ON j.student_id=s.id
        """
        summary_params = []
        if start:
            summary_query += " AND j.attendance_date >= %s"; summary_params.append(start)
        if end:
            summary_query += " AND j.attendance_date <= %s"; summary_params.append(end)
        summary_query += " WHERE (%s = '' OR u.id::text = %s) GROUP BY s.id,s.full_name,s.workplace ORDER BY s.full_name"
        summary_params.extend([class_id, class_id])
        summary = conn.execute(summary_query, summary_params).fetchall()
    if not rows:
        flash("Tidak ada jurnal untuk diekspor dengan filter tersebut.", "warning")
        return redirect(url_for("report", class_id=class_id, start=start, end=end, student_id=student_id))

    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=13*mm, leftMargin=13*mm,
                            topMargin=12*mm, bottomMargin=17*mm,
                            title="Laporan Jurnal Kegiatan Prakerin")
    styles = getSampleStyleSheet()
    school = ParagraphStyle("School", parent=styles["Normal"], fontName="Helvetica-Bold",
                            fontSize=10, leading=12, alignment=TA_CENTER)
    heading = ParagraphStyle("HeadingReport", parent=styles["Normal"], fontName="Helvetica-Bold",
                             fontSize=11, leading=14, alignment=TA_CENTER)
    body = ParagraphStyle("Cell", parent=styles["Normal"], fontName="Helvetica",
                          fontSize=7, leading=9, alignment=TA_LEFT, wordWrap="CJK")
    center = ParagraphStyle("CellCenter", parent=body, alignment=TA_CENTER)
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8, leading=11)
    story = []
    story += [Paragraph("LAPORAN JURNAL KEGIATAN PRAKERIN", heading),
              Spacer(1, 5*mm)]
    dates = [r["attendance_date"] for r in rows]
    month_names = ["Januari","Februari","Maret","April","Mei","Juni","Juli","Agustus","September","Oktober","November","Desember"]
    def indo_date(value):
        return f"{value.day} {month_names[value.month-1]} {value.year}"
    first = rows[0]
    student_name = profile["full_name"] if student_id and profile and profile.get("full_name") else ""
    class_name = profile["class_name"] if profile else first["class_name"]
    # Bulan rekap mengikuti bulan data jurnal yang sedang diekspor.
    months = sorted({(value.year, value.month) for value in dates})
    month_label = ", ".join(
        f"{month_names[month - 1]} {year}" for year, month in months
    )
    recap_date = indo_date(datetime.now().astimezone().date())
    info = [
        [Paragraph("<b>Tanggal Rekap</b>", small), Paragraph(": "+escape(recap_date), small)],
        [Paragraph("<b>Bulan Rekap</b>", small), Paragraph(": "+escape(month_label), small)],
        [Paragraph("<b>Kelas</b>", small), Paragraph(": "+escape(str(class_name or "-")), small)],
    ]
    info_table=Table(info,colWidths=[28*mm,156*mm],hAlign="LEFT")
    info_table.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),0),
                                    ("TOPPADDING",(0,0),(-1,-1),1),("BOTTOMPADDING",(0,0),(-1,-1),1)]))
    story += [info_table, Spacer(1, 2*mm)]
    if student_id and student_name:
        story += [Paragraph("Nama Siswa: <b>" + escape(str(student_name)) + "</b>", small), Spacer(1, 3*mm)]
    else:
        story.append(Spacer(1, 2*mm))
    if student_id:
        headers = [Paragraph("<b>Timestamp</b>",center),Paragraph("<b>Kegiatan</b>",center),
                   Paragraph("<b>Foto</b>",center)]
        data=[headers]
        for row in rows:
            timestamp = row.get("submitted_at") or "-"
            activity=(row.get("activity") or row.get("note") or "-").strip()
            img_cell=Paragraph("-",body)
            photo_name = row.get("photo_filename") or ""
            if photo_name:
                try:
                    from PIL import Image as PILImage
                    photo_bytes, _content_type = load_photo(photo_name)
                    im=PILImage.open(BytesIO(photo_bytes)); im.thumbnail((420,300))
                    bio=BytesIO(); im.convert("RGB").save(bio,format="JPEG",quality=75); bio.seek(0)
                    img_cell=RLImage(bio,width=38*mm,height=27*mm)
                except Exception:
                    img_cell=Paragraph("Foto tersedia",center)
            data.append([Paragraph(escape(str(timestamp)),center),
                         Paragraph(escape(activity).replace("\\n","<br/>"),body),img_cell])
        table=Table(data,colWidths=[42*mm,82*mm,48*mm],repeatRows=1,hAlign="CENTER")
    else:
        headers = [Paragraph("<b>Nama Siswa</b>",center),Paragraph("<b>Tempat PKL</b>",center),Paragraph("<b>Izin</b>",center),
                   Paragraph("<b>Sakit</b>",center),Paragraph("<b>Hadir</b>",center),
                   Paragraph("<b>Total</b>",center)]
        data=[headers]
        for row in summary:
            data.append([Paragraph(escape(str(row["full_name"])),body),
                         Paragraph(escape(str(row.get("workplace") or "-")),body),
                         Paragraph(str(row["izin"]),center),Paragraph(str(row["sakit"]),center),
                         Paragraph(str(row["hadir"]),center),Paragraph(str(row["total"]),center)])
        data.append([Paragraph("<b>TOTAL KELAS</b>",body),Paragraph("-",center),
                     Paragraph(str(sum(r["izin"] for r in summary)),center),
                     Paragraph(str(sum(r["sakit"] for r in summary)),center),
                     Paragraph(str(sum(r["hadir"] for r in summary)),center),
                     Paragraph(str(sum(r["total"] for r in summary)),center)])
        table=Table(data,colWidths=[58*mm,55*mm,16*mm,16*mm,19*mm,18*mm],repeatRows=1,hAlign="CENTER")
    table.setStyle(TableStyle([
        ("GRID",(0,0),(-1,-1),0.55,colors.black),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eeeeee")),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
    ]))
    story += [table, Spacer(1, 4*mm)]
    def page_num(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica",7)
        canvas.drawCentredString(A4[0]/2,8*mm,f"Halaman {document.page}")
        canvas.restoreState()
    doc.build(story,onFirstPage=page_num,onLaterPages=page_num)
    output.seek(0)
    return send_file(output,mimetype="application/pdf",as_attachment=True,
                     download_name="laporan_jurnal_prakerin.pdf")

@app.route("/admin/rekap.csv")
@login_required
@admin_required
def report_csv():
    class_id = request.args.get("class_id", "")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    student_id = request.args.get("student_id", "")
    params = []
    if student_id:
        query = """
            SELECT j.submitted_at,j.activity,j.note,j.photo_filename,s.workplace
            FROM journals j JOIN students s ON s.id=j.student_id
            JOIN users u ON u.id=s.class_user_id
            WHERE j.student_id=%s
        """
        params.append(student_id)
        if class_id:
            query += " AND u.id=%s"; params.append(class_id)
        if start:
            query += " AND j.attendance_date >= %s"; params.append(start)
        if end:
            query += " AND j.attendance_date <= %s"; params.append(end)
        query += " ORDER BY j.submitted_at DESC"
        headers = ["Tempat PKL", "Timestamp", "Kegiatan", "Foto"]
    else:
        query = """
            SELECT s.full_name,s.workplace,
                   COUNT(j.id) FILTER (WHERE j.status='Izin') AS izin,
                   COUNT(j.id) FILTER (WHERE j.status='Sakit') AS sakit,
                   COUNT(j.id) FILTER (WHERE j.status='Hadir') AS hadir,
                   COUNT(j.id) AS total
            FROM students s JOIN users u ON u.id=s.class_user_id
            LEFT JOIN journals j ON j.student_id=s.id
        """
        if start:
            query += " AND j.attendance_date >= %s"; params.append(start)
        if end:
            query += " AND j.attendance_date <= %s"; params.append(end)
        query += " WHERE (%s = '' OR u.id::text = %s)"
        params.extend([class_id, class_id])
        query += " GROUP BY s.id,s.full_name ORDER BY s.full_name"
        headers = ["Nama Siswa", "Tempat PKL", "Izin", "Sakit", "Hadir", "Total"]
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    if student_id:
        for row in rows:
            writer.writerow([row["workplace"] or "-", row["submitted_at"], row["activity"] or row["note"] or "-", row["photo_filename"] or "-"])
    else:
        for row in rows:
            writer.writerow([row["full_name"], row["workplace"] or "-", row["izin"], row["sakit"], row["hadir"], row["total"]])
        writer.writerow(["TOTAL KELAS", "-",
                         sum(row["izin"] for row in rows),
                         sum(row["sakit"] for row in rows),
                         sum(row["hadir"] for row in rows),
                         sum(row["total"] for row in rows)])
    return Response("\\ufeff" + output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":"attachment; filename=rekap_jurnal_pkl.csv"})

@app.route("/foto/<path:filename>")
@login_required
def photo(filename):
    # Foto hanya dapat dilihat oleh admin atau pemilik jurnal.
    with get_db() as conn:
        row = conn.execute("SELECT user_id FROM journals WHERE photo_filename=%s", (filename,)).fetchone()
    if not row:
        abort(404)
    if session["role"] != "admin" and row["user_id"] != session["user_id"]:
        abort(403)
    try:
        photo_bytes, content_type = load_photo(filename)
    except FileNotFoundError:
        abort(404)
    return Response(photo_bytes, mimetype=content_type,
                    headers={"Cache-Control": "private, no-store",
                             "X-Content-Type-Options": "nosniff"})

@app.errorhandler(413)
def too_large(_):
    flash("Ukuran foto maksimal 4 MB.", "danger")
    return redirect(request.referrer or url_for("dashboard"))

@app.errorhandler(403)
def forbidden(_):
    return render_template("error.html", code=403, message="Anda tidak memiliki akses."), 403



_db_init_lock = __import__("threading").Lock()
_db_initialized = False

@app.before_request
def ensure_database_initialized():
    global _db_initialized
    if not _db_initialized:
        with _db_init_lock:
            if not _db_initialized:
                if os.environ.get("VERCEL"):
                    if not os.environ.get("DATABASE_URL"):
                        raise RuntimeError("DATABASE_URL belum dikonfigurasi di Vercel.")
                    if app.config["SECRET_KEY"] == "ganti-secret-key-ini-sebelum-deploy":
                        raise RuntimeError("SECRET_KEY wajib diatur sebelum deployment publik.")
                init_db()
                _db_initialized = True

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
