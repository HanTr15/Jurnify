# Jurnify — Vercel-ready

Versi ini disiapkan untuk deploy Flask ke Vercel, PostgreSQL ke Neon, dan foto jurnal ke **Vercel Blob Private Store**. Foto tetap dilayani melalui route `/foto/...` setelah pemeriksaan login dan kepemilikan jurnal; URL Blob private tidak dibuka langsung ke publik.

## Perubahan deployment

- Flask `app.py` berada di root repository agar terdeteksi otomatis oleh Vercel.
- Aset dari `static/` juga disalin ke `public/static/` untuk disajikan melalui CDN Vercel.
- Foto baru diunggah ke Vercel Blob dengan akses `private`; nama/path Blob disimpan di kolom `journals.photo_filename`.
- Route foto memeriksa hak akses melalui database sebelum mengunduh foto.
- Export PDF mengambil foto dari Blob, bukan filesystem lokal.
- Database dan migrasi tabel dijalankan otomatis saat request pertama pada setiap instance.
- Penyimpanan lokal tetap digunakan saat dijalankan di luar Vercel tanpa konfigurasi Blob.
- Batas upload foto disetel 4 MB agar berada di bawah batas body request server upload Vercel.

## 1. Siapkan Neon PostgreSQL

1. Buat project di https://neon.tech.
2. Buat database dan salin connection string PostgreSQL.
3. Simpan connection string untuk environment variable `DATABASE_URL`. Gunakan connection string pooled Neon jika tersedia dan pastikan SSL aktif.

## 2. Siapkan repository GitHub

Ekstrak ZIP ini. Pastikan `app.py`, `requirements.txt`, `templates/`, `public/`, dan `.python-version` berada langsung di root repository — jangan deploy folder induk yang hanya berisi folder project lain.

Contoh:

```text
jurnify/
├── app.py
├── requirements.txt
├── .python-version
├── .vercelignore
├── public/
│   └── static/
├── static/
├── templates/
└── uploads/
```

Push ke GitHub:

```bash
git init
git add .
git commit -m "Prepare Jurnify for Vercel"
git branch -M main
git remote add origin https://github.com/USERNAME/jurnify.git
git push -u origin main
```

## 3. Buat project Vercel

1. Buka https://vercel.com/new dan import repository GitHub.
2. Pastikan Root Directory menunjuk ke root folder yang berisi `app.py`.
3. Framework akan terdeteksi sebagai Flask/Python. Jangan mengisi Build Command atau Output Directory khusus.
4. Deploy pertama setelah environment variables di bawah sudah disiapkan, atau tambahkan variabel lalu lakukan Redeploy.

## 4. Buat Vercel Blob Private Store

1. Buka project Vercel → Storage → Create Storage → Blob.
2. Pilih **Private** (bukan Public), lalu buat store.
3. Hubungkan store ke project Jurnify untuk Production dan Preview.
4. Pastikan integrasi menambahkan `BLOB_STORE_ID` dan autentikasi OIDC Vercel ke Function. Jika menggunakan token manual, tambahkan `BLOB_READ_WRITE_TOKEN` sebagai environment variable rahasia.
5. Jangan pernah menaruh token Blob di source code, file frontend, atau repository.

Aplikasi menggunakan Python SDK `vercel-blob` dan mode private. Jika store dibuat Public, implementasi ini tidak akan cocok dengan mode akses yang dipilih.

## 5. Atur Environment Variables

Di Vercel → Project → Settings → Environment Variables, tambahkan:

| Variable | Isi |
|---|---|
| `DATABASE_URL` | Connection string Neon PostgreSQL |
| `SECRET_KEY` | Secret acak panjang, minimal 32 byte |
| `ADMIN_PASSWORD` | Password admin awal yang kuat |
| `BLOB_STORE_ID` | Otomatis dari koneksi Blob Private |
| `VERCEL_OIDC_TOKEN` | Dikelola Vercel; jangan isi manual |

Untuk membuat secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`BLOB_STORE_ID` dan OIDC biasanya ditambahkan otomatis saat Blob Store dihubungkan. Jika project memakai static token, gunakan `BLOB_READ_WRITE_TOKEN` yang disediakan Vercel dan simpan sebagai secret.

Tambahkan `DATABASE_URL`, `SECRET_KEY`, dan `ADMIN_PASSWORD` ke Production dan Preview sesuai kebutuhan. Setelah itu lakukan Redeploy.

**Penting:** `ADMIN_PASSWORD` hanya digunakan ketika database belum memiliki akun `admin`. Setelah akun dibuat, ubah password admin dari mekanisme pengelolaan akun aplikasi yang tersedia. Jangan membagikan environment variables.

## 6. Deploy dan cek

Setelah deployment selesai, buka URL `*.vercel.app`. Request pertama akan membuat/memperbarui tabel database dan membuat akun admin awal jika belum ada.

Login admin awal:
- Username: `admin`
- Password: nilai `ADMIN_PASSWORD` yang kamu set di Vercel.

Segera ganti password awal setelah login.

Tes alur berikut sebelum membagikan ke siswa:
1. Login admin dan buat akun kelas serta data siswa.
2. Login akun kelas, kirim jurnal dengan foto.
3. Pastikan jurnal muncul pada verifikasi dan foto dapat dibuka.
4. Coba akses URL foto tanpa login; harus diarahkan ke login.
5. Login sebagai akun lain dan coba akses foto milik kelas lain; harus mendapat 403.
6. Export PDF per kelas dan per siswa; pastikan foto muncul pada PDF per siswa.
7. Export CSV, logout, lalu login kembali.
8. Redeploy satu kali dan pastikan foto lama tetap dapat dibuka.

## 7. Hubungkan domain sendiri

1. Vercel → Project → Settings → Domains.
2. Masukkan domain atau subdomain yang ingin digunakan, misalnya `jurnal.domainkamu.id`.
3. Ikuti record DNS yang diberikan Vercel di panel DNS penyedia domain.
4. Tunggu verifikasi DNS dan sertifikat HTTPS aktif.
5. Uji login dan pengiriman jurnal melalui domain tersebut.

## Catatan penting

- Jangan menyimpan foto jurnal di filesystem lokal Vercel. Filesystem Function bersifat sementara; foto disimpan di Vercel Blob.
- Gunakan Blob **Private** karena foto jurnal siswa tidak boleh dapat dibuka publik.
- Jangan commit `.env`, token, atau connection string.
- Migrasi otomatis berjalan ketika aplikasi menerima request pertama. Pastikan `DATABASE_URL` sudah benar sebelum trafik publik diarahkan.
- Versi ini tidak otomatis memindahkan foto lama yang masih ada di folder `uploads/` ke Blob. Jika ada data foto lama, migrasikan dulu dari server lama dan perbarui referensi `photo_filename` sebelum mematikan server lama.
- Uji export PDF dan foto dengan data nyata sebelum dipakai operasional sekolah.
