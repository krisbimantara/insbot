# PRODUCT REQUIREMENTS DOCUMENT
## Sistem Inspeksi Kendaraan
### Integrasi Frappe ERPNext & Telegram Bot

> Versi 1.0 | 2025

---

## 1. Ringkasan Eksekutif

Dokumen ini menjelaskan kebutuhan produk untuk sistem inspeksi kendaraan yang mengintegrasikan Custom App Frappe (ERPNext) dengan Telegram Bot. Sistem ini dirancang untuk mempercepat dan menstandarisasi proses inspeksi motor tarikan oleh inspektor lapangan.

| Atribut | Detail |
|---|---|
| Nama Proyek | Sistem Inspeksi Kendaraan via Telegram |
| Platform Utama | Frappe / ERPNext (Custom App) + Telegram Bot |
| Target Pengguna | Inspektor lapangan, Admin/Supervisor |
| Total Komponen Inspeksi | 65 komponen tetap + maks. 4 conditional (total maks. 69) |
| Total Foto Wajib | 10 foto per sesi inspeksi |
| Versi Dokumen | 1.0 |

---

## 2. Latar Belakang & Tujuan

### 2.1 Latar Belakang

Proses inspeksi motor tarikan saat ini dilakukan secara manual sehingga rentan terhadap inkonsistensi data, kehilangan informasi, dan lambatnya pelaporan. Dibutuhkan sistem digital yang memudahkan inspektor lapangan melakukan inspeksi terstruktur melalui perangkat yang sudah mereka gunakan sehari-hari (Telegram), sekaligus mengintegrasikan hasilnya langsung ke database Frappe.

### 2.2 Tujuan Produk

- Menstandarisasi proses inspeksi kendaraan dengan checklist terstruktur 65+ komponen.
- Mempercepat pencatatan hasil inspeksi langsung dari lapangan via Telegram.
- Mengintegrasikan data inspeksi secara real-time ke Custom App Frappe.
- Memastikan akuntabilitas inspektor melalui sistem autentikasi berbasis Telegram ID.
- Menyediakan mekanisme revisi per kategori untuk menjaga akurasi data.

---

## 3. Stakeholder & Pengguna

| Peran | Platform | Tanggung Jawab |
|---|---|---|
| Admin / Supervisor | Frappe Web App | Input Motor Tarikan, request inspeksi, monitor hasil |
| Inspektor Lapangan | Telegram Bot | Menerima tugas, mengisi checklist, mengirim foto |
| Sistem Bot | Telegram + Middleware | Mengelola alur percakapan, validasi, sinkronisasi data |
| Frappe Backend | Custom App | Menyimpan data Motor Tarikan & Hasil Inspeksi |

---

## 4. Arsitektur Sistem

### 4.1 Gambaran Umum

Sistem terdiri dari tiga lapisan utama yang saling terhubung:

| Lapisan | Komponen | Teknologi |
|---|---|---|
| Frontend / Chat | Telegram Bot | aiogram (Python), Inline Keyboard |
| Middleware / API | Bot Service + Session Manager | FastAPI / Express, Redis |
| Backend / Database | Frappe Custom App | Frappe Framework, MariaDB, Cloud Storage |

### 4.2 Stack Teknologi

- **Bot Framework:** aiogram (Python) — async, cocok untuk produksi
- **Session State:** Redis (dengan TTL untuk mencegah data stale)
- **Koneksi ke Frappe:** Frappe REST API dengan API Key & Secret
- **Penyimpanan Foto:** Frappe File Manager via REST upload
- **Hosting Bot:** Docker container (Berada di server yang sama dengan container custom app frappe)
- **Webhook:** HTTPS wajib (nginx + SSL / reverse proxy)

---

## 5. Desain Doctype Frappe

### 5.1 Doctype: Motor Tarikan (modifikasi)

Tambahkan field berikut pada Doctype Motor Tarikan yang sudah ada:

| Field Name | Label | Type | Keterangan |
|---|---|---|---|
| telegram_inspector_id | Telegram Inspector ID | Data | Telegram user_id inspektor yang ditugaskan |
| status_inspeksi | Status Inspeksi | Select | Pilihan: Belum / Menunggu / Selesai |

### 5.2 Doctype: Hasil Inspeksi (baru)

Doctype baru untuk menampung seluruh hasil inspeksi dari Telegram Bot.

#### 5.2.1 Field Identitas & Relasi

| Field Name | Label | Type | Keterangan |
|---|---|---|---|
| motor_tarikan | Motor Tarikan | Link | Link ke Doctype Motor Tarikan |
| inspector | Inspektor | Link | Link ke Doctype User |
| tanggal_inspeksi | Tanggal Inspeksi | Datetime | Waktu submit hasil |
| status | Status | Select | Draft / Submitted |

#### 5.2.2 Kategori 1 — Body & Rangka (9 field)

| Field Name | Label | Pilihan |
|---|---|---|
| kepala | Kepala | Baik / Cukup / Rusak |
| sayap_dalam | Sayap Dalam (Sepasang) | Baik / Cukup / Rusak |
| sayap_luar | Sayap Luar (Sepasang) | Baik / Cukup / Rusak |
| rangka_tengah | Rangka Tengah | Baik / Cukup / Rusak |
| body_belakang | Body Belakang | Baik / Cukup / Rusak |
| spakboard_depan | Spakboard Depan | Baik / Cukup / Rusak |
| spakboard_belakang | Spakboard Belakang | Baik / Cukup / Rusak |
| leher_angsa | Leher Angsa | Baik / Cukup / Rusak |
| list_grafis | List Grafis (1 Set) | Baik / Cukup / Rusak |

#### 5.2.3 Kategori 2 — Mesin (13 field)

| Field Name | Label | Pilihan |
|---|---|---|
| crankcase_assy | Crankcase Assy | Baik / Cukup / Rusak |
| head_cylinder | Head Cylinder | Baik / Cukup / Rusak |
| cylinder | Cylinder | Baik / Cukup / Rusak |
| carburator_assy | Carburator Assy | Baik / Cukup / Rusak |
| oil_pump_assy | Oil Pump Assy | Baik / Cukup / Rusak |
| cover_crankcase_1 | Cover Crankcase 1 | Baik / Cukup / Rusak |
| cover_crankcase_2 | Cover Crankcase 2 | Baik / Cukup / Rusak |
| rantai_kamrat | Rantai Kamrat | Baik / Cukup / Rusak |
| crankshaft_assy | Crankshaft Assy | Baik / Cukup / Rusak |
| gear_rantai_vbelt | Gear dan Rantai (1 Set) / V-Belt | Baik / Cukup / Rusak |
| muffler_knalpot | Muffler (Knalpot) | Baik / Cukup / Rusak |
| fuel_tank | Fuel Tank | Baik / Cukup / Rusak |
| bahan_bakar | Bahan Bakar | E / 1/4 / 1/2 / 3/4 / F |

#### 5.2.4 Kategori 3 — Kelistrikan (10 field)

| Field Name | Label | Pilihan |
|---|---|---|
| accu | Accu | Baik / Cukup / Rusak |
| cdi | CDI | Baik / Cukup / Rusak |
| kiprok | Kiprok | Baik / Cukup / Rusak |
| main_switch_steering_lock | Main Switch Steering Lock | Baik / Cukup / Rusak |
| ignition_coil | Ignition Coil | Baik / Cukup / Rusak |
| dinamo_stater | Dinamo Stater | Baik / Cukup / Rusak |
| rotor_magnet | Rotor / Magnet | Baik / Cukup / Rusak |
| stator_kumparan | Stator / Kumparan | Baik / Cukup / Rusak |
| klakson | Klakson | Baik / Cukup / Rusak |
| speedometer | Speedometer | Baik / Cukup / Rusak |

#### 5.2.5 Kategori 4 — Lampu & Sein (4 field)

| Field Name | Label | Pilihan |
|---|---|---|
| lampu_depan | Lampu Depan | Baik / Cukup / Rusak |
| lampu_belakang | Lampu Belakang | Baik / Cukup / Rusak |
| sein_depan | Sein Depan (Sepasang) | Baik / Cukup / Rusak |
| sein_belakang | Sein Belakang (Sepasang) | Baik / Cukup / Rusak |

#### 5.2.6 Kategori 5 — Kaki-kaki & Rem (14 field)

| Field Name | Label | Pilihan |
|---|---|---|
| shock_belakang | Shock Belakang (Pair) | Baik / Cukup / Rusak |
| inner_tube_depan | Inner Tube Comp Dpn (Pair) | Baik / Cukup / Rusak |
| master_cakram | Master Cakram (1 Set) | Baik / Cukup / Rusak |
| plate_brake_shoe | Plate Brake Shoe | Baik / Cukup / Rusak |
| piringan_rem_depan | Piringan Rem Depan | Baik / Cukup / Rusak |
| master_cylinder_rem | Master Cylinder Rem | Baik / Cukup / Rusak |
| kampas_cakram | Kampas Cakram | Baik / Cukup / Rusak |
| kampas_tromol | Kampas Tromol | Baik / Cukup / Rusak |
| tires_depan | Karet (Tires) Depan | Baik / Cukup / Rusak |
| tires_belakang | Karet (Tires) Belakang | Baik / Cukup / Rusak |
| velg_cw_depan | Velg CW Depan | Baik / Cukup / Rusak |
| velg_cw_belakang | Velg CW Belakang | Baik / Cukup / Rusak |
| velg_jarjari_depan | Velg Jari-jari Depan | Baik / Cukup / Rusak |
| velg_jarjari_belakang | Velg Jari-jari Belakang | Baik / Cukup / Rusak |

#### 5.2.7 Kategori 6 — Aksesori & Kelengkapan (12 field)

| Field Name | Label | Pilihan |
|---|---|---|
| kaca_spion | Kaca Spion | Baik / Cukup / Rusak |
| tool_kit | Tool Kit | Baik / Cukup / Rusak |
| tool_box | Tool Box | Baik / Cukup / Rusak |
| tutup_rantai_vbelt | Tutup Rantai / V-Belt | Baik / Cukup / Rusak |
| panel_instrumen_kanan | Panel Instrumen Kanan | Baik / Cukup / Rusak |
| panel_instrumen_kiri | Panel Instrumen Kiri | Baik / Cukup / Rusak |
| jok_tempat_duduk | Jok Tempat Duduk | Baik / Cukup / Rusak |
| behel_belakang | Behel Belakang | Baik / Cukup / Rusak |
| foot_step_depan | Foot Step Depan | Baik / Cukup / Rusak |
| foot_step_belakang | Foot Step Belakang | Baik / Cukup / Rusak |
| segitiga_atas | Segitiga Atas | Baik / Cukup / Rusak |
| segitiga_bawah | Segitiga Bawah | Baik / Cukup / Rusak |

#### 5.2.8 Kategori 7 — Kick & Pedal (3 field)

| Field Name | Label | Pilihan |
|---|---|---|
| kick_starter | Kick Starter | Baik / Cukup / Rusak |
| pedal_gigi | Pedal Gigi | Baik / Cukup / Rusak |
| pedal_rem | Pedal Rem | Baik / Cukup / Rusak |

#### 5.2.9 Kategori 8 — Dokumen & Conditional STNK

| Field Name | Label | Type | Keterangan |
|---|---|---|---|
| stnk | STNK | Select | Baik / Cukup / Rusak (trigger conditional) |
| stnk_hilang_polisi | STNK Hilang (Polisi) | Select | Ya / Tidak — muncul jika STNK = Cukup atau Rusak (opsional) |
| stnk_tilang | STNK Tilang | Select | Ya / Tidak — muncul jika STNK = Cukup atau Rusak (opsional) |
| stnk_ta | STNK T/A | Select | Ya / Tidak — muncul HANYA jika STNK = Rusak (opsional) |
| stnk_mati_tanggal | Tanggal Akhir STNK | Date | Muncul jika STNK = Cukup atau Rusak (opsional) |

#### 5.2.10 Field Foto (10 field)

| Field Name | Label | Type |
|---|---|---|
| foto_tampak_depan | Foto Tampak Depan | Attach Image |
| foto_tampak_belakang | Foto Tampak Belakang | Attach Image |
| foto_tampak_kanan | Foto Tampak Kanan | Attach Image |
| foto_tampak_kiri | Foto Tampak Kiri | Attach Image |
| foto_mesin | Foto Mesin | Attach Image |
| foto_nomor_rangka | Foto Nomor Rangka | Attach Image |
| foto_nomor_mesin | Foto Nomor Mesin | Attach Image |
| foto_stnk | Foto STNK | Attach Image |
| foto_ban_depan | Foto Ban Depan | Attach Image |
| foto_ban_belakang | Foto Ban Belakang | Attach Image |

---

## 6. Flow Telegram Bot

### 6.1 Flow Fase 1 — Trigger dari Frappe ke Telegram

Admin dapat menekan tombol Request Inspeksi untuk satu atau lebih Motor Tarikan sekaligus. Setiap motor yang di-request akan didaftarkan ke antrian tugas inspektor yang bersangkutan.

1. Admin membuka Doctype Motor Tarikan di Frappe.
2. Admin mengisi field Telegram Inspector ID dengan Telegram user_id inspektor yang ditugaskan.
3. Admin menekan tombol custom Request Inspeksi pada form Motor Tarikan. Tombol ini dapat ditekan untuk lebih dari satu Motor Tarikan secara terpisah.
4. Frappe Server Script (hooks / custom button) mengirim POST request ke Bot Middleware untuk setiap motor yang di-request.
5. Bot Middleware menerima payload berisi: motor_id, info kendaraan, telegram_id inspektor, lalu mendaftarkan motor tersebut ke daftar tugas inspektor di Redis.
6. Bot mengirimkan notifikasi ke inspektor di Telegram bahwa ada tugas inspeksi baru yang menunggu.

> **Contoh pesan notifikasi:**
> ```
> 🔔 Tugas Inspeksi Baru!
> Kamu memiliki 3 motor yang perlu diinspeksi.
> Ketuk [Lihat Daftar Motor] untuk memilih.
> ```

7. Status setiap Motor Tarikan yang di-request otomatis berubah menjadi **Menunggu** di Frappe.

### 6.2 Flow Fase 1.5 — Pemilihan Motor (Layer Pertama)

Layer ini adalah pintu masuk sebelum proses inspeksi dimulai. Inspektor wajib memilih motor mana yang akan diinspeksi terlebih dahulu.

1. Inspektor menekan tombol **Lihat Daftar Motor** pada notifikasi, atau mengetik `/mulai` kapan saja.
2. Bot mengambil daftar Motor Tarikan dari Frappe via REST API, dengan filter: `telegram_inspector_id = ID inspektor AND status_inspeksi != Selesai`.
3. Bot menampilkan daftar motor sebagai Inline Keyboard Button, satu baris per motor:

> **Contoh tampilan daftar motor:**
> ```
> Pilih motor yang akan diinspeksi:
> [ 🚵 Honda Beat 2020 — BP 1234 XX ]
> [ 🚵 Yamaha Mio 2019 — BP 5678 YY ]
> [ 🚵 Honda Vario 2021 — BP 9012 ZZ ]
> ```

4. Daftar hanya menampilkan motor dengan `status_inspeksi` bukan **Selesai** (Belum atau Menunggu).
5. Jika tidak ada motor yang perlu diinspeksi, bot menampilkan pesan: *Tidak ada tugas inspeksi yang tersisa.*
6. Inspektor memilih salah satu motor dari daftar — bot menyimpan `motor_id` yang dipilih ke session Redis dan melanjutkan ke Fase 2.
7. Jika inspektor memiliki sesi inspeksi yang belum selesai untuk motor tersebut (data di Redis), bot menawarkan pilihan: **Lanjutkan sesi sebelumnya** atau **Mulai ulang**.

### 6.3 Flow Fase 2 — Proses Inspeksi di Telegram

1. Inspektor memilih motor dari daftar — bot menampilkan konfirmasi motor yang dipilih dan tombol **Mulai Inspeksi**.
2. Bot menampilkan menu utama inspeksi dengan progress per kategori:
   - Setiap kategori dikerjakan secara berurutan.
   - Kategori berikutnya terbuka setelah kategori sebelumnya selesai.
   - Jika inspektor keluar di tengah sesi, progress tersimpan di Redis dan dapat dilanjutkan.
3. Untuk setiap komponen, bot menampilkan pertanyaan dengan Inline Keyboard pilihan jawaban.
4. Setiap jawaban disimpan ke session state (Redis).
5. Bot menampilkan progress bar di setiap pertanyaan, contoh: `[████████░░] 8/10`.
6. Khusus komponen STNK, berlaku aturan conditional sebagai berikut:
   - **Baik:** tidak ada pertanyaan tambahan.
   - **Cukup:** muncul 3 pertanyaan conditional (opsional/skippable): STNK Hilang, STNK Tilang, Tanggal Mati STNK.
   - **Rusak:** muncul 4 pertanyaan conditional (opsional/skippable): STNK Hilang, STNK Tilang, STNK T/A, Tanggal Mati STNK.
7. Setelah semua pertanyaan selesai, bot masuk ke sesi pengiriman 10 foto secara berurutan.
   - Setiap foto dipandu dengan instruksi kategori yang jelas.
   - Setelah foto dikirim, inspektor dapat mengonfirmasi atau mengulang foto (Foto Ulang).

### 6.4 Flow Fase 3 — Review & Revisi

1. Setelah semua kategori dan foto selesai, bot menampilkan halaman **Ringkasan Inspeksi**.
2. Ringkasan memperlihatkan status tiap kategori (jumlah komponen terisi) dan status foto.
3. Inspektor dapat memilih **Revisi Kategori** untuk mengulang kategori tertentu:
   - Bot menampilkan daftar kategori yang bisa dipilih.
   - Setelah memilih, bot menampilkan ulang pertanyaan satu per satu dengan jawaban sebelumnya tampil sebagai referensi.
   - Inspektor dapat menjawab ulang atau menekan **Skip** untuk mempertahankan jawaban lama.
   - Jika jawaban trigger STNK diubah, bot menginformasikan perubahan pada pertanyaan conditional.
4. Setelah revisi selesai, bot kembali ke halaman Ringkasan dengan tanda **Direvisi** pada kategori yang diubah.
5. Inspektor menekan **Kirim Hasil** untuk menyelesaikan inspeksi.

### 6.5 Flow Fase 4 — Sinkronisasi ke Frappe

1. Bot mengompilasi seluruh jawaban dan foto dari session Redis.
2. Bot mengirim POST request ke Frappe REST API endpoint `/api/resource/Hasil Inspeksi`.
3. Frappe membuat dokumen baru Hasil Inspeksi dengan relasi ke Motor Tarikan.
4. Foto diunggah ke Frappe File Manager via REST API dan URL-nya disimpan di field foto.
5. Frappe mengirimkan konfirmasi balik ke Bot.
6. Status Motor Tarikan diperbarui menjadi **Selesai**.
7. Bot mengirimkan notifikasi konfirmasi ke inspektor: *Hasil inspeksi berhasil disimpan.*

---

## 7. Sistem Autentikasi & Akses

Bot Telegram hanya merespons pesan dari Telegram ID yang terdaftar di Frappe. Mekanisme yang digunakan adalah AuthMiddleware yang melakukan pengecekan dinamis ke Frappe REST API setiap kali pesan masuk.

| Aspek | Detail |
|---|---|
| Metode Auth | Middleware pengecekan Telegram ID ke database Frappe |
| Pendaftaran Inspektor | Dilakukan oleh Admin di Frappe (tanpa perlu ubah kode bot) |
| Respons jika tidak terdaftar | Bot menolak pesan dan menampilkan pesan akses ditolak |
| Pencabutan Akses | Admin cukup menonaktifkan / menghapus Telegram ID di Frappe |

---

## 8. Manajemen Session State

Setiap inspektor memiliki session state yang disimpan di Redis untuk mempertahankan progress inspeksi lintas sesi percakapan.

| Field State | Tipe | Keterangan |
|---|---|---|
| telegram_id | Integer | ID unik Telegram inspektor |
| pending_motors | List | Daftar motor_id yang masih dalam antrian tugas inspektor (didaftarkan saat Fase 1, diperbarui setiap motor selesai) |
| motor_id | String | ID dokumen Motor Tarikan yang sedang aktif diinspeksi |
| inspection_started | Boolean | Flag apakah sesi inspeksi sudah dimulai — mencegah pembatalan setelah proses berjalan |
| mode | String | inspeksi / revisi / ringkasan |
| current_category | String | Nama kategori yang sedang aktif dikerjakan |
| current_question | String | ID pertanyaan yang sedang aktif dalam kategori tersebut |
| answers | Dict | Seluruh jawaban komponen yang sudah diisi (termasuk nilai null untuk conditional STNK yang di-skip) |
| stnk_answer | String | Nilai jawaban STNK utama (Baik/Cukup/Rusak) — digunakan untuk menentukan pertanyaan conditional yang muncul |
| photo_index | Integer | Urutan foto yang sedang dikerjakan (0–9) — menjaga posisi sesi pengiriman 10 foto secara berurutan |
| photos | Dict | File ID Telegram untuk setiap kategori foto (key: nama kategori foto, value: file_id) |
| completed_categories | List | Daftar kategori checklist yang sudah selesai dikerjakan |
| progress | Dict | Detail progres per kategori (done, total) |
| revision_history | Dict | Riwayat kategori yang telah direvisi (key: nama kategori, value: timestamp revisi) — digunakan untuk tanda Direvisi pada ringkasan dan audit trail |
| revisi_kategori | String | Nama kategori yang sedang direvisi (hanya aktif jika mode = revisi) |

---

## 9. Ringkasan Checklist Komponen

| # | Kategori | Jumlah Komponen | Jenis Pilihan |
|---|---|---|---|
| 1 | Body & Rangka | 9 | Baik / Cukup / Rusak |
| 2 | Mesin | 12 + 1 khusus | Baik/Cukup/Rusak; Bahan Bakar: E/1/4/1/2/3/4/F |
| 3 | Kelistrikan | 10 | Baik / Cukup / Rusak |
| 4 | Lampu & Sein | 4 | Baik / Cukup / Rusak |
| 5 | Kaki-kaki & Rem | 14 | Baik / Cukup / Rusak |
| 6 | Aksesori & Kelengkapan | 12 | Baik / Cukup / Rusak |
| 7 | Kick & Pedal | 3 | Baik / Cukup / Rusak |
| 8 | Dokumen (STNK) | 1 + maks. 4 conditional | Baik/Cukup/Rusak + Ya/Tidak/Tanggal |
| | **TOTAL** | **65 + maks. 4 conditional = 69** | |
| | Foto Wajib | 10 foto per kategori | Upload gambar |

---

## 10. Kebutuhan Non-Fungsional

| Aspek | Kebutuhan |
|---|---|
| Ketersediaan | Bot harus online 24/7 dengan uptime minimal 99% |
| Performa | Respons bot maksimal 2 detik setelah input pengguna |
| Keamanan | Semua komunikasi via HTTPS; API Key Frappe tidak disimpan di kode (env variable) |
| Skalabilitas | Mendukung minimal 10 sesi inspeksi berjalan secara bersamaan |
| Persistensi | Session Redis dengan TTL 24 jam untuk mencegah data stale |
| Audit Trail | Setiap submit inspeksi mencatat waktu, inspektor, dan riwayat revisi |
