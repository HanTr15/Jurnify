<div align="center">

<img src="static/img/jurnify-logo.png" alt="Jurnify Logo" width="240" />

# Jurnify — Sistem Jurnal & Presensi PKL Modern Berbasis AI & Cloud

[![Python Version](https://img.shields.io/badge/python-3.12-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/framework-Flask%203.x-black.svg?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Database](https://img.shields.io/badge/database-PostgreSQL%20%2F%20Neon-00e599.svg?logo=postgresql&logoColor=white)](https://neon.tech/)
[![Cloud Storage](https://img.shields.io/badge/storage-Vercel%20Blob%20(Private)-000000.svg?logo=vercel&logoColor=white)](https://vercel.com/docs/storage/vercel-blob)
[![AI Engine](https://img.shields.io/badge/AI-Google%20Gemini-4285F4.svg?logo=googlegemini&logoColor=white)](https://aistudio.google.com/)
[![Deployment](https://img.shields.io/badge/deploy-Vercel%20Serverless-000000.svg?logo=vercel&logoColor=white)](https://vercel.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

*Solusi all-in-one digitalisasi monitoring kegiatan Praktik Kerja Lapangan (PKL) / Magang Industri untuk SMK dan Perguruan Tinggi dengan validasi lokasi GPS, otomasi AI Vision, dan arsitektur serverless yang hemat biaya.*

---

[Fitur Utama](#-fitur-utama) • 
[Arsitektur](#-arsitektur--alur-sistem) • 
[Tech Stack](#-tech-stack) • 
[Instalasi Lokal](#-instalasi-lokal) • 
[Panduan Deployment](#-panduan-deployment-ke-vercel) • 
[Environment Variables](#-konfigurasi-environment-variables) • 
[Struktur Direktori](#-struktur-direktori)

</div>

---

## 🌟 Tentang Jurnify

**Jurnify** dirancang untuk memecahkan kendala klasik pengelolaan jurnal PKL konvensional (buku cetak fisik) yang rentan rusak, sulit dipantau secara real-time, dan memakan waktu saat rekapitulasi nilai akhir. 

Dengan menggabungkan kemudahan akses web responsif, validasi koordinat GPS presisi radius DUDI (Dunia Usaha & Dunia Industri), AI Vision dari Google Gemini untuk import data massal dari foto kertas absensi, serta penyimpanan cloud privat berkeamanan tinggi, Jurnify memberikan pengalaman pencatatan kegiatan magang yang transparan, akuntabel, dan terstruktur bagi siswa maupun pihak sekolah/pembimbing.

---

## ✨ Fitur Utama

### 📍 1. Presensi & Jurnal Kegiatan Harian
- **Status Kehadiran Fleksibel:** Mendukung status *Hadir*, *Izin*, *Sakit*, maupun *Libur/WFH*.
- **GPS Radius Validation (Anti-Fake Location):** Menghitung jarak langsung antara posisi perangkat siswa saat mengirim jurnal dengan titik koordinat DUDI menggunakan rumus **Haversine**. Siswa akan diperingatkan jika berada di luar radius toleransi.
- **Unggah Foto Bukti Otentik:** Dokumentasi visual kegiatan kerja dengan kompresi cerdas dan limit keamanan request (maksimal 4 MB).

### 🤖 2. Integrasi Google Gemini AI
- **Smart OCR Scanner (Foto ke Data Siswa):** Cukup foto lembar daftar nama/absensi fisik siswa, Gemini Vision AI akan mengekstrak nama, NIS, dan penempatan lokasi PKL secara instan ke tabel terstruktur siap impor.
- **Admin AI Assistant (Chatbot):** Fitur asisten cerdas untuk guru/admin guna menganalisis keaktifan jurnal, membantu rekapitulasi data, serta memberikan rekomendasi evaluasi bimbingan PKL.

### 🛡️ 3. Keamanan & Proteksi Privasi Tingkat Tinggi
- **Vercel Blob Private Store:** Foto jurnal siswa berstatus *private* dan tidak memiliki URL publik langsung. Akses foto wajib melalui endpoint terproteksi (`/foto/...`) dengan verifikasi otentikasi sesi dan hak akses (mencegah celah IDOR).
- **Proteksi CSRF Global:** Setiap request mutasi data diamankan dengan token CSRF (`Flask-WTF`).
- **Enkripsi Kredensial Akun Kelas:** Menggunakan enkripsi simetris (Fernet via `cryptography`) dan HMAC SHA-256 untuk kredensial kelas yang aman.
- **Role-Based Access Control (RBAC):** Pemisahan ketat hak akses antara Administrator (Guru/Pembimbing) dan Akun Kelas/Siswa.

### 📑 4. Verifikasi & Alur Bimbingan Interaktif
- **Review Real-time:** Guru pembimbing dapat menyetujui (*Approve*) atau menolak (*Reject*) jurnal harian disertai catatan masukan/revisi.
- **Pembaruan Jurnal:** Siswa dapat memperbaiki jurnal jika statusnya memerlukan revisi dari pembimbing.

### 📦 5. Smart Monthly Archive & Auto-Purge Storage
- **Kompilasi Arsip Bulanan:** Menghasilkan dokumen PDF rekap per kelas yang menyematkan seluruh ringkasan kehadiran, deskripsi tugas, dan **foto bukti asli secara embedded**.
- **Penyimpanan di PostgreSQL (`BYTEA`):** Arsip PDF disimpan langsung di database, sehingga aman dari karakteristik serverless filesystem ephemeral.
- **Pembersihan Storage Terjadwal:** Fitur "Bersihkan Data" memungkinkan penghapusan foto asli dari Vercel Blob untuk menghemat kuota penyimpanan tanpa kehilangan arsip visual.

### 📊 6. Laporan & Ekspor Data Siap Cetak
- **Ekspor PDF Rekapitulasi Kelas:** Laporan rapi siap cetak untuk diserahkan ke pihak kurikulum sekolah/industri.
- **Ekspor PDF Individual Siswa:** Dilengkapi foto kegiatan per hari untuk lampiran laporan akhir magang.
- **Ekspor CSV Spreadsheet:** Memudahkan rekap nilai dan analisis data di Microsoft Excel atau Google Sheets.

---

## 🏗️ Arsitektur & Alur Sistem

```mermaid
flowchart TD
    subgraph Client["Pengguna (Browser / Mobile)"]
        Siswa["Siswa / Akun Kelas"]
        Admin["Admin / Guru Pembimbing"]
    end

    subgraph Edge["Vercel Edge & CDN"]
        CDN["Vercel CDN (/public/static)"]
        Router["Vercel Serverless Function (Flask WSGI)"]
    end

    subgraph Backend["Core Application (Python 3.12)"]
        App["Flask (app.py)"]
        Auth["Auth & CSRF (Flask-WTF / Fernet)"]
        Geo["Haversine GPS Verification"]
        PDFGen["ReportLab & Pillow Engine"]
    end

    subgraph External["External Cloud Services"]
        NeonDB[("Neon Serverless PostgreSQL\n(Pooled Connection)")]
        BlobStore[("Vercel Blob\n(Private Storage)")]
        GeminiAI["Google Gemini 1.5 / 2.0\n(Vision OCR & Chatbot)"]
    end

    Siswa -->|Submit Jurnal + Foto + GPS| Router
    Admin -->|Review Jurnal / Scan OCR / Rekap| Router
    Router --> App
    App --> Auth
    App --> Geo
    App --> PDFGen
    
    App <-->|Read / Write Data & PDF Bytea| NeonDB
    App <-->|Upload / Stream Foto Terproteksi| BlobStore
    App <-->|Prompt Chatbot / Analisis Gambar OCR| GeminiAI
```

---

## 💻 Tech Stack

| Layer | Komponen / Teknologi | Keterangan |
|---|---|---|
| **Backend** | Python 3.12, Flask 3.x | Lightweight, cepat, dan kompatibel penuh dengan Serverless |
| **Security** | Flask-WTF, Cryptography | CSRF protection, token handling, enkripsi kredensial |
| **Database** | PostgreSQL via `psycopg` (v3) | Dikelola pada Neon Serverless dengan connection pooler |
| **Cloud Storage** | `@vercel/blob` (Python SDK) | Penyimpanan file gambar terproteksi (Private Mode) |
| **AI / LLM** | Google Gemini API | Multimodal vision untuk OCR berkas & AI Chatbot Assistant |
| **PDF Engine** | ReportLab, Pillow (PIL) | Generator laporan PDF dinamis dan arsip bulanan |
| **Frontend** | Jinja2 Templates, Vanilla CSS, JS | Ringan, responsif diakses dari smartphone siswa |
| **Hosting** | Vercel Serverless Functions | Auto-scaling tanpa beban maintenance server fisik |

---

## 📂 Struktur Direktori

```text
jurnify/
├── .env.example             # Contoh template environment variables
├── .gitignore               # Daftar pengecualian Git
├── .python-version          # Versi Python yang digunakan Vercel (3.12)
├── .vercelignore            # Berkas yang dikecualikan saat build Vercel
├── app.py                   # Aplikasi inti Flask (Routing, Business Logic, DB Migration)
├── requirements.txt         # Daftar dependency library Python
├── vercel.json              # Konfigurasi routing Vercel
├── public/                  # Static assets publik yang disajikan CDN Vercel
│   └── static/
├── static/                  # Berkas statis aplikasi
│   ├── css/
│   │   └── style.css        # Styling responsif UI
│   ├── img/                 # Aset grafis (Logo, Icon, Banner)
│   └── school_crest.png     # Logo sekolah untuk kop laporan PDF
├── templates/               # Berkas HTML Jinja2
│   ├── base.html            # Layout master & navbar
│   ├── dashboard_admin.html # Dashboard utama guru/pembimbing
│   ├── dashboard_student.html# Dashboard jurnal siswa
│   ├── journal_form.html    # Formulir entri jurnal & validasi GPS
│   ├── login.html           # Halaman autentikasi sistem
│   ├── monthly_archives.html# Panel manajemen arsip & purge storage
│   ├── report.html          # Halaman filter & ekspor rekapitulasi
│   ├── students.html        # Manajemen siswa & alat scan foto AI
│   ├── verification.html    # Halaman persetujuan/penolakan jurnal
│   └── chatbot.html         # Antarmuka Chatbot AI Admin
└── uploads/                 # Direktori fallback lokal (khusus development non-Vercel)
```

---

## ⚙️ Konfigurasi Environment Variables

Buat berkas `.env` di root proyek (atau isi pada panel Vercel Settings):

| Variabel | Tipe | Wajib? | Deskripsi & Contoh |
|---|:---:|:---:|---|
| `DATABASE_URL` | String | **Ya** | PostgreSQL URI dari Neon (contoh: `postgresql://user:pass@ep-xyz.neon.tech/neondb?sslmode=require`) |
| `SECRET_KEY` | String | **Ya** | Kunci rahasia untuk sesi cookie & enkripsi token (minimal 32 byte acak) |
| `ADMIN_PASSWORD` | String | **Ya** | Password awal untuk akun bawaan `admin` saat database pertama kali diinisialisasi |
| `GEMINI_API_KEY` | String | Opsional | Kunci API dari Google AI Studio untuk mengaktifkan AI Vision OCR & Chatbot |
| `GEMINI_MODEL` | String | Opsional | Model Gemini yang digunakan (default: `gemini-flash-lite-latest` atau `gemini-1.5-flash`) |
| `BLOB_STORE_ID` | String | Otomatis | Terisi otomatis saat Vercel Blob Store dihubungkan ke project |
| `BLOB_READ_WRITE_TOKEN`| String | Khusus | Token manual jika Anda tidak menggunakan sistem Vercel OIDC otomatis |

> [!TIP]
> **Cara menghasilkan `SECRET_KEY` yang kuat:**
> ```bash
> python3 -c "import secrets; print(secrets.token_urlsafe(48))"
> ```

---

## 🚀 Instalasi Lokal (Local Development)

Jika Anda ingin menjalankan atau mengembangkan proyek ini di komputer lokal:

### 1. Kloning Repository
```bash
git clone https://github.com/HanTr15/Jurnify.git
cd Jurnify
```

### 2. Siapkan Python Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate  # Di Windows: venv\Scripts\activate
```

### 3. Pasang Dependensi
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Konfigurasi `.env`
Salin template konfigurasi dan sesuaikan nilai variabelnya:
```bash
cp .env.example .env
```
*(Pastikan Anda telah mengisi `DATABASE_URL` mengarah ke database PostgreSQL lokal atau instance gratis Neon).*

### 5. Jalankan Server Development
```bash
python3 app.py
```
Akses aplikasi melalui browser di: `http://127.0.0.1:5000`

---

## ☁️ Panduan Deployment ke Vercel

Jurnify telah dioptimalkan secara native untuk serverless deployment di Vercel:

### 1. Siapkan Database Neon PostgreSQL
1. Buka [neon.tech](https://neon.tech) dan buat project baru.
2. Salin **Connection String** PostgreSQL (pilih mode *Pooled connection* untuk efisiensi serverless).

### 2. Hubungkan Repository ke Vercel
1. Buka [Vercel Dashboard](https://vercel.com/new) dan import repository `Jurnify`.
2. Pastikan **Framework Preset** terdeteksi sebagai `Other` atau `Flask`.
3. Biarkan *Build Command* dan *Output Directory* kosong (sesuai setelan default).

### 3. Buat Vercel Blob Store (Private Mode)
1. Pada menu dashboard proyek di Vercel, buka tab **Storage** → klik **Create Database** → pilih **Blob**.
2. **PENTING:** Pilih tipe **Private Store** (bukan Public) karena foto jurnal siswa dilindungi hak akses.
3. Hubungkan (*Connect*) Blob Store tersebut ke deployment *Production* dan *Preview*. Vercel akan otomatis menyuntikkan token integrasi.

### 4. Masukkan Environment Variables di Vercel
Pada menu **Settings** → **Environment Variables**, masukkan:
- `DATABASE_URL` : URL koneksi Neon PostgreSQL.
- `SECRET_KEY` : String acak hasil generate.
- `ADMIN_PASSWORD` : Kata sandi untuk login awal akun `admin`.
- `GEMINI_API_KEY` : Kunci API Google Gemini (dari [Google AI Studio](https://aistudio.google.com/apikey)).
- `GEMINI_MODEL` : `gemini-flash-lite-latest`

### 5. Deploy & Inisialisasi Database
1. Klik tombol **Deploy** (atau *Redeploy*).
2. Setelah deployment berstatus *Ready*, kunjungi domain URL `*.vercel.app` Anda.
3. Aplikasi akan secara otomatis melakukan migrasi tabel database dan membuat akun admin pertama pada request pertama.
4. Login menggunakan akun:
   - **Username:** `admin`
   - **Password:** Nilai yang Anda set di `ADMIN_PASSWORD`
5. Segera perbarui kata sandi admin melalui panel kelola akun demi keamanan.

---

## 🧪 Alur Pengujian & Verifikasi Fitur

Sebelum mendistribusikan akun ke siswa, lakukan checklist berikut:
- [ ] **Login Admin:** Masuk dengan username `admin` dan pastikan menu berjalan lancar.
- [ ] **Setup Kelas & Siswa:** Buat satu akun kelas dan tambahkan data siswa beserta titik koordinat PKL.
- [ ] **Test OCR Scan:** Uji fitur scan foto absensi dengan mengunggah gambar daftar siswa untuk memastikan Gemini API bekerja.
- [ ] **Input Jurnal Siswa:** Login menggunakan akun kelas, input jurnal kegiatan harian beserta foto dokumentasi.
- [ ] **Validasi Geolocation:** Cek apakah penghitungan jarak (Haversine) mendeteksi lokasi dengan akurat.
- [ ] **Verifikasi Admin:** Buka menu verifikasi pada akun admin, berikan persetujuan (*Approve*) atau revisi.
- [ ] **Uji Akses Foto Terproteksi:** Coba salin URL foto `/foto/...` lalu buka di tab *Incognito* tanpa login. Sistem harus mengarahkan ke halaman login (tidak bisa diakses publik secara bebas).
- [ ] **Ekspor Laporan & Arsip:** Unduh Rekapitulasi PDF dan pastikan foto tertanam dengan baik di lembar dokumen.

---

## 🔒 Praktik Terbaik Keamanan

1. **Jangan Menyimpan Berkas di Filesystem Vercel:** Filesystem Vercel Serverless bersifat *ephemeral* (sementara). Seluruh file foto wajib menggunakan Vercel Blob Private Store.
2. **Kerahasiaan `.env`:** Jangan pernah melakukan `git commit` pada berkas `.env` asli atau membocorkan `DATABASE_URL` dan `SECRET_KEY`.
3. **Rotasi Kredensial:** Selalu ubah password awal `ADMIN_PASSWORD` sesaat setelah sistem pertama kali dipasang di production.

---

## 🤝 Berkontribusi

Kontribusi selalu terbuka! Jika Anda memiliki ide perbaikan, penambahan fitur, atau menemukan kendala (bug):
1. Fork repository ini
2. Buat feature branch baru (`git checkout -b feature/FiturKeren`)
3. Commit perubahan Anda (`git commit -m 'feat: Tambahkan fitur presensi QR code'`)
4. Push ke branch Anda (`git push origin feature/FiturKeren`)
5. Ajukan **Pull Request**

---

## 📄 Lisensi

Proyek ini dirilis di bawah lisensi [MIT](LICENSE). Silakan gunakan, pelajari, dan modifikasi untuk kebutuhan edukasi di sekolah atau instansi Anda.

---

<div align="center">
  <p>Dikembangkan dengan ❤️ untuk kemudahan monitoring PKL siswa SMK & Vokasi Indonesia.</p>
  <sub>© 2026 Jurnify. All rights reserved.</sub>
</div>
