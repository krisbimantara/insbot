# Requirements Document

## Introduction

Telegram Inspection Bot adalah middleware berbasis aiogram (Python async) yang menjembatani Custom App Frappe ERPNext dengan inspektor lapangan melalui Telegram. Bot menerima trigger dari Frappe (via webhook) ketika admin meminta inspeksi sebuah Motor Tarikan, memandu inspektor melalui checklist terstruktur (66 komponen wajib + maksimum 4 pertanyaan conditional STNK = maksimum 70 jawaban) dan pengambilan 10 foto wajib, kemudian mengirim hasil kembali ke Frappe melalui REST API.

Bot mendukung dua tipe inspeksi: `Inspeksi` (inspeksi pertama) dan `Inspeksi Ulang` (re-inspeksi). Otentikasi inspektor dilakukan dinamis terhadap Frappe pada setiap pesan masuk (tanpa allowlist statis), session disimpan di Redis dengan TTL 24 jam, dan satu inspektor dapat memiliki banyak motor pending dalam antrian.

Dokumen ini mendefinisikan kebutuhan fungsional, non-fungsional, kontrak integrasi dengan Frappe, dan penanganan kondisi error/edge case untuk Bot. Implementasi sisi Frappe (custom app, doctype, server script) di luar lingkup dokumen ini dan diasumsikan sudah tersedia sesuai `API_DOCUMENTATION.md`.

## Glossary

- **Bot**: Aplikasi Telegram Inspection Bot itu sendiri (proses aiogram + HTTP webhook server). Digunakan sebagai subjek requirement: "THE Bot SHALL ...".
- **Inspector**: Pengguna Telegram yang `telegram_id`-nya terdaftar pada field `telegram_inspector_id` di salah satu dokumen Motor Tarikan di Frappe.
- **Admin**: Pengguna Frappe yang men-trigger Request Inspeksi untuk sebuah Motor Tarikan. Tidak berinteraksi dengan Bot secara langsung.
- **Frappe**: Backend Custom App Frappe ERPNext yang mengekspos REST API (`get_pending_list`, `upload_foto`, `submit_hasil_inspeksi`) dan menerbitkan webhook `inspection_requested`.
- **Motor Tarikan**: Doctype di Frappe yang merepresentasikan satu unit motor untuk diinspeksi. Diidentifikasi oleh field `name` (mis. `PJ-001`).
- **Hasil Inspeksi**: Doctype di Frappe yang menampung hasil inspeksi tersubmit. Dibuat oleh Bot via `submit_hasil_inspeksi`.
- **Inspection Session**: State per (telegram_id, motor_tarikan) yang disimpan di Redis: jawaban komponen, photo file_id, progress, mode, dll. Lihat field state pada PRD §8.
- **Pending Queue**: Daftar `motor_tarikan` yang ditugaskan ke seorang inspektor dan belum berstatus `Selesai Inspeksi`, disimpan di Redis (`pending_motors`) dan dapat di-refresh dari Frappe.
- **Checklist**: Kumpulan 66 komponen wajib yang dikelompokkan ke 8 kategori, masing-masing dengan opsi jawaban tetap. Lihat lampiran kontrak data (Requirement 14).
- **Conditional STNK**: Maksimum 4 pertanyaan tambahan yang muncul ketika jawaban field `stnk` bernilai `Cukup` atau `Rusak`. Bersifat skippable (boleh kosong saat submit).
- **Photo Set**: Sepuluh foto wajib (lihat Requirement 14) yang harus dikirim inspektor sebelum submit.
- **Webhook Inspection Request**: HTTP POST yang dikirim Frappe ke `POST /webhook/inspection-request` dengan event `inspection_requested`.
- **Auth Middleware**: Komponen aiogram yang memvalidasi setiap update Telegram terhadap Frappe sebelum handler dijalankan.
- **Idempotency Key**: String unik per percobaan submit (mis. `{telegram_id}:{motor_tarikan}:{session_started_at}`) yang memungkinkan retry tanpa membuat dokumen Hasil Inspeksi duplikat.
- **Reply Keyboard**: Custom keyboard Telegram yang muncul menggantikan keyboard standar di bawah area input. Tombol yang ditekan akan terkirim sebagai pesan teks biasa. Bot menggunakan Reply Keyboard untuk **input jawaban inspeksi** (mis. Baik/Cukup/Rusak, E/1/4/1/2/3/4/F, Ya/Tidak, Skip) agar inspektor tidak perlu mengetik bebas.
- **Inline Keyboard**: Tombol yang menempel pada pesan tertentu dan mengirim `callback_data` saat ditekan (tidak meninggalkan jejak teks di chat). Bot menggunakan Inline Keyboard untuk **navigasi/aksi alur** (mis. pemilihan motor, Mulai Inspeksi, Konfirmasi/Foto Ulang, daftar Revisi Kategori, Kirim Hasil).
- **EARS**: Easy Approach to Requirements Syntax — pola kalimat requirement yang dipakai dokumen ini.

## Requirements

### Requirement 1: Penerimaan Webhook Permintaan Inspeksi dari Frappe

**User Story:** Sebagai Admin di Frappe, saya ingin Bot menerima notifikasi otomatis ketika saya menekan tombol Request Inspeksi, sehingga inspektor yang ditugaskan langsung mendapat pesan Telegram tanpa perlu intervensi manual.

#### Acceptance Criteria

1. THE Bot SHALL menyediakan HTTP endpoint `POST /webhook/inspection-request` yang menerima body JSON dengan field `event`, `motor_tarikan`, `nopol`, `merk`, `model`, `tahun`, `warna`, `tipe_inspeksi`, dan `inspector_chat_id`.
2. WHEN payload webhook diterima dengan `event = "inspection_requested"` dan seluruh field wajib terisi non-kosong, THE Bot SHALL menambahkan `motor_tarikan` ke `pending_motors` inspektor di Redis (tanpa duplikasi) dan mengirim pesan notifikasi Telegram ke `inspector_chat_id`.
3. WHEN pesan notifikasi dikirim, THE Bot SHALL menyertakan `merk`, `model`, `tahun`, `nopol`, `tipe_inspeksi`, dan instruksi untuk mengetik `/mulai`.
4. IF payload webhook memiliki `event` selain `"inspection_requested"`, THEN THE Bot SHALL merespons HTTP 400 dengan body `"Unknown event"` dan tidak mengirim pesan Telegram.
5. IF payload webhook tidak memiliki `inspector_chat_id` atau `motor_tarikan`, THEN THE Bot SHALL merespons HTTP 400 dengan body yang menyebutkan field yang hilang dan tidak mengubah Redis.
6. IF pengiriman pesan Telegram ke `inspector_chat_id` gagal (mis. user belum pernah `/start` ke bot, chat diblokir), THEN THE Bot SHALL tetap merespons HTTP 200 ke Frappe, tetap mempertahankan `motor_tarikan` di `pending_motors`, dan mencatat error pada log dengan level `WARNING`.
7. THE Bot SHALL merespons webhook valid dengan HTTP 200 dalam waktu maksimum 5 detik diukur dari penerimaan request.
8. WHEN endpoint webhook menerima payload identik dua kali untuk `motor_tarikan` yang sudah ada di `pending_motors` inspektor tersebut, THE Bot SHALL tidak menduplikasi entri di Redis dan tetap mengirim ulang notifikasi Telegram. (Aturan idempotensi ini hanya berlaku terhadap penerimaan webhook; pengulangan aksi yang berasal dari sumber lain tidak diperlakukan sebagai duplikasi webhook.)

### Requirement 2: Otentikasi Dinamis Inspektor (Auth Middleware)

**User Story:** Sebagai Admin, saya ingin Bot hanya merespons inspektor yang telah saya daftarkan di Frappe, sehingga akses dapat dicabut hanya dengan menghapus `telegram_inspector_id` di Frappe tanpa redeploy bot.

#### Acceptance Criteria

1. WHEN sebuah update Telegram (message, callback_query) diterima dari `telegram_id`, THE Bot SHALL memanggil endpoint `get_pending_list` Frappe dengan parameter `telegram_id` tersebut sebelum menjalankan handler bisnis.
2. WHERE response Frappe untuk `get_pending_list` mengembalikan HTTP 200 dengan `message.ok = true`, THE Bot SHALL menganggap `telegram_id` terotorisasi dan melanjutkan eksekusi handler.
3. IF response Frappe mengembalikan HTTP 200 dengan `message.ok = false` atau body yang menunjukkan `PermissionError`, THEN THE Bot SHALL memperlakukan inspektor sebagai tidak terotorisasi (tolak akses) sebagaimana klausul 4.
4. IF response Frappe mengembalikan HTTP 403 atau `exc_type = "PermissionError"`, THEN THE Bot SHALL membalas inspektor dengan pesan `"Akses ditolak. Hubungi admin."` dan menghentikan pemrosesan update tanpa mengubah session Redis.
5. IF panggilan `get_pending_list` gagal karena network error atau HTTP 5xx, THEN THE Bot SHALL membalas inspektor dengan pesan `"Sistem sedang sibuk, silakan coba lagi sebentar."` dan tidak menulis ke Redis.
6. WHERE update yang masuk berasal dari endpoint webhook Frappe (bukan dari Telegram), THE Bot SHALL melewati Auth Middleware karena otentikasi sudah dilakukan via header API Key Frappe. WHEN webhook merujuk pada `inspector_chat_id` yang tidak terotorisasi di Frappe (mis. dihapus setelah penugasan), THE Bot SHALL tetap mengirim pesan error ke `inspector_chat_id` tersebut yang menjelaskan akses ditolak alih-alih diam.
7. THE Bot SHALL melakukan caching hasil otorisasi `telegram_id` di memori proses dengan TTL maksimum 60 detik untuk menghindari pemanggilan Frappe berulang dalam burst pesan dari user yang sama.

### Requirement 3: Daftar Motor Pending dan Pemilihan Motor Aktif

**User Story:** Sebagai Inspector, saya ingin melihat daftar motor yang ditugaskan kepada saya dan memilih satu untuk diinspeksi, sehingga saya dapat menangani antrian inspeksi secara berurutan.

#### Acceptance Criteria

1. WHEN inspektor mengirim perintah `/mulai` atau menekan tombol `Lihat Daftar Motor`, THE Bot SHALL memanggil `get_pending_list` Frappe dan menggunakan `data` array sebagai sumber kebenaran untuk daftar motor pending.
2. THE Bot SHALL menampilkan setiap entri sebagai Inline Keyboard Button satu baris berformat `{merk} {model} {tahun} — {nopol}` dengan `callback_data` berisi `motor_tarikan`. (Pemilihan motor adalah navigasi, sehingga menggunakan Inline Keyboard.)
3. WHERE `data` array kosong, THE Bot SHALL menampilkan pesan `"Tidak ada tugas inspeksi yang tersisa."` dan tidak menampilkan keyboard.
4. WHEN inspektor menekan tombol motor, THE Bot SHALL menyimpan `motor_id` ke Inspection Session di Redis dan menampilkan kartu konfirmasi berisi `nopol`, `merk`, `model`, `tahun`, `warna`, dan `tipe_inspeksi` dengan tombol `Mulai Inspeksi` (Inline Keyboard, karena ini aksi navigasi).
5. IF inspektor sudah memiliki Inspection Session aktif (`inspection_started = true`) untuk `motor_id` yang sama, THEN THE Bot SHALL menampilkan dua tombol Inline Keyboard: `Lanjutkan Sesi Sebelumnya` dan `Mulai Ulang`.
6. WHEN inspektor memilih `Mulai Ulang`, THE Bot SHALL menghapus seluruh field session untuk motor tersebut kecuali `pending_motors`, kemudian memulai sesi baru.
7. THE Bot SHALL me-refresh `pending_motors` di Redis dengan hasil `data` dari `get_pending_list` setiap kali daftar ditampilkan, sehingga motor yang sudah selesai/dihapus admin tidak lagi muncul.
8. WHEN `get_pending_list` mengembalikan motor dengan `status_inspeksi = "Proses Inspeksi Ulang"`, THE Bot SHALL menyimpan `tipe_inspeksi = "Inspeksi Ulang"` ke session; selain itu THE Bot SHALL menyimpan `tipe_inspeksi = "Inspeksi"`.

### Requirement 4: Eksekusi Checklist Komponen Berurutan per Kategori

**User Story:** Sebagai Inspector, saya ingin mengisi checklist komponen secara terstruktur kategori demi kategori dengan progress yang jelas, sehingga saya tidak melewatkan komponen apapun.

#### Acceptance Criteria

1. THE Bot SHALL menggunakan urutan kategori tetap: 1) Body & Rangka, 2) Mesin, 3) Kelistrikan, 4) Lampu & Sein, 5) Kaki-kaki & Rem, 6) Aksesori & Kelengkapan, 7) Kick & Pedal, 8) Dokumen (STNK).
2. WHEN sebuah kategori aktif, THE Bot SHALL menampilkan komponen-komponen kategori tersebut satu per satu dalam urutan tetap sebagaimana didefinisikan pada Requirement 14.
3. THE Bot SHALL menampilkan setiap pertanyaan komponen sebagai pesan dengan label komponen, indikator progress berformat `[████████░░] {done}/{total}` (granularitas per-komponen dalam keseluruhan checklist), dan **Reply Keyboard** berisi pilihan jawaban (one-time keyboard, resize_keyboard=true) agar inspektor cukup menekan tombol tanpa mengetik bebas.
4. THE Bot SHALL menggunakan opsi jawaban `Baik / Cukup / Rusak` (3 tombol Reply Keyboard) untuk seluruh komponen kecuali `bahan_bakar` yang menggunakan opsi `E / 1/4 / 1/2 / 3/4 / F` (5 tombol Reply Keyboard).
5. WHEN inspektor mengirim teks dari Reply Keyboard, THE Bot SHALL memvalidasi bahwa teks tersebut anggota set opsi yang valid untuk komponen aktif; IF teks bukan anggota set yang valid (mis. inspektor mengetik manual nilai lain), THEN THE Bot SHALL menampilkan ulang pertanyaan dengan Reply Keyboard yang sama dan tidak menyimpan jawaban.
6. WHEN inspektor memilih jawaban valid via Reply Keyboard, THE Bot SHALL menyimpan `(field_name, value)` ke `answers` di Redis terlebih dahulu, dan HANYA setelah penyimpanan sukses melanjutkan ke pertanyaan komponen berikutnya. IF penyimpanan ke Redis gagal, THEN THE Bot SHALL menampilkan pesan `"Gagal menyimpan jawaban, silakan coba lagi."` dan menampilkan ulang pertanyaan yang sama tanpa memajukan posisi.
7. WHEN seluruh komponen di sebuah kategori telah dijawab, THE Bot SHALL menambahkan nama kategori ke `completed_categories` dan menampilkan pesan transisi ke kategori berikutnya.
8. IF inspektor mengirim pesan teks bebas yang bukan opsi Reply Keyboard saat sebuah pertanyaan komponen aktif, THEN THE Bot SHALL mengabaikan teks dan menampilkan ulang pertanyaan beserta Reply Keyboard pilihan jawaban.
9. THE Bot SHALL mempertahankan `current_category` dan `current_question` di Redis setelah setiap interaksi sehingga sesi dapat dilanjutkan ketika inspektor kembali.
10. WHEN sesi inspeksi berakhir (submit sukses, motor dibatalkan sebelum mulai, atau berpindah ke fase lain yang tidak butuh input pilihan), THE Bot SHALL menyertakan `ReplyKeyboardRemove` pada pesan berikutnya untuk membersihkan Reply Keyboard dari layar inspektor.

### Requirement 5: Pertanyaan Conditional STNK

**User Story:** Sebagai Inspector, saya ingin pertanyaan tambahan tentang STNK hanya muncul jika kondisi STNK tidak `Baik`, sehingga inspeksi cepat ketika STNK lengkap dan tetap detail ketika ada masalah.

#### Acceptance Criteria

1. WHEN inspektor menjawab `stnk = Baik`, THE Bot SHALL melewati seluruh pertanyaan conditional STNK dan menyelesaikan kategori Dokumen.
2. WHEN inspektor menjawab `stnk = Cukup`, THE Bot SHALL menampilkan tiga pertanyaan conditional secara berurutan: `stnk_hilang_polisi` (Ya/Tidak), `stnk_tilang` (Ya/Tidak), `stnk_mati_tanggal` (Date YYYY-MM-DD).
3. WHEN inspektor menjawab `stnk = Rusak`, THE Bot SHALL menampilkan empat pertanyaan conditional secara berurutan: `stnk_hilang_polisi` (Ya/Tidak), `stnk_tilang` (Ya/Tidak), `stnk_ta` (Ya/Tidak), `stnk_mati_tanggal` (Date YYYY-MM-DD).
4. THE Bot SHALL menampilkan pertanyaan conditional bertipe Ya/Tidak (`stnk_hilang_polisi`, `stnk_tilang`, `stnk_ta`) dengan **Reply Keyboard** berisi tombol `Ya`, `Tidak`, dan `Skip`. WHEN inspektor menekan `Skip`, THE Bot SHALL menyimpan nilai `null` (atau menghilangkan key) untuk field tersebut di `answers` dan melanjutkan.
5. THE Bot SHALL menampilkan pertanyaan `stnk_mati_tanggal` dengan **Reply Keyboard** berisi satu tombol `Skip` dan menerima input tanggal sebagai pesan teks bebas dari inspektor. WHEN inspektor mengirim teks untuk `stnk_mati_tanggal`, THE Bot SHALL menerima nilai hanya jika cocok dengan regex `^\d{4}-\d{2}-\d{2}$` dan tanggal valid; IF format tidak valid, THEN THE Bot SHALL menampilkan pesan error format dan meminta ulang input dengan Reply Keyboard `Skip` yang sama. WHEN inspektor menekan `Skip`, THE Bot SHALL menyimpan `null` dan melanjutkan.
6. WHEN inspektor merevisi jawaban `stnk` melalui flow revisi (Requirement 7) sehingga set pertanyaan conditional yang seharusnya muncul berubah, THE Bot SHALL segera menghapus jawaban conditional yang tidak lagi relevan dari `answers` pada saat revisi tersebut disimpan dan langsung menampilkan pertanyaan conditional yang baru relevan tanpa menunggu inspektor bernavigasi kembali.
7. WHILE `stnk_answer = "Baik"`, THE Bot SHALL tidak menampilkan pertanyaan conditional STNK manapun dan tidak menyertakan field `stnk_hilang_polisi`, `stnk_tilang`, `stnk_ta`, atau `stnk_mati_tanggal` dalam payload, terlepas dari nilai sisa di `answers` dari sesi sebelumnya.
8. THE Bot SHALL menyimpan `stnk_answer` di session terpisah dari `answers["stnk"]` dengan nilai yang selalu identik, sehingga logika branching conditional dapat dievaluasi ulang tanpa mengubah `answers`.

### Requirement 6: Pengambilan 10 Foto Wajib

**User Story:** Sebagai Inspector, saya ingin Bot memandu saya mengambil 10 foto wajib satu per satu dengan instruksi yang jelas, sehingga semua sudut motor terdokumentasi sebelum submit.

#### Acceptance Criteria

1. WHEN seluruh komponen checklist (termasuk conditional STNK yang relevan) telah dijawab, THE Bot SHALL memulai sesi pengambilan foto dengan `photo_index = 0` dan menyertakan `ReplyKeyboardRemove` pada pesan pertama fase foto agar Reply Keyboard checklist tidak mengganggu pengambilan foto.
2. THE Bot SHALL meminta foto dalam urutan tetap: `foto_tampak_depan`, `foto_tampak_belakang`, `foto_tampak_kanan`, `foto_tampak_kiri`, `foto_mesin`, `foto_nomor_rangka`, `foto_nomor_mesin`, `foto_stnk`, `foto_ban_depan`, `foto_ban_belakang`.
3. WHEN Bot meminta sebuah foto, THE Bot SHALL menampilkan label foto, deskripsi singkat, dan progress `Foto {photo_index+1}/10`.
4. WHEN inspektor mengirim foto via Telegram (sebagai photo atau document image), THE Bot SHALL menyimpan `file_id` Telegram di `photos[field_name]` dan menampilkan **Inline Keyboard** dengan dua tombol: `Konfirmasi` dan `Foto Ulang` (aksi navigasi, bukan input data).
5. WHEN inspektor menekan `Foto Ulang`, THE Bot SHALL menghapus `photos[field_name]` dan meminta ulang foto yang sama tanpa memajukan `photo_index`.
6. WHEN inspektor menekan `Konfirmasi`, THE Bot SHALL menambah `photo_index` sebesar 1 dan meminta foto berikutnya hingga `photo_index = 10`.
7. IF inspektor mengirim file non-image (mis. video, dokumen non-image, sticker) saat sesi foto aktif, THEN THE Bot SHALL menampilkan pesan `"Mohon kirim foto (JPG/PNG)."` dan tetap pada `photo_index` yang sama.
8. IF foto yang dikirim memiliki ukuran > 5 MB setelah Bot mengunduh dari Telegram, THEN THE Bot SHALL melakukan kompresi sisi-bot (downscale + JPEG quality reduction) hingga ukuran ≤ 5 MB sebelum upload ke Frappe.
9. WHEN seluruh 10 foto sudah dikonfirmasi, THE Bot SHALL melanjutkan ke fase Ringkasan (Requirement 7).

### Requirement 7: Ringkasan Inspeksi dan Revisi per Kategori

**User Story:** Sebagai Inspector, saya ingin melihat ringkasan jawaban dan foto saya sebelum kirim, dengan opsi merevisi kategori tertentu, sehingga saya bisa memperbaiki kesalahan tanpa mengulang seluruh inspeksi.

#### Acceptance Criteria

1. WHEN seluruh foto telah dikonfirmasi, THE Bot SHALL menampilkan halaman Ringkasan berisi: nama motor, daftar 8 kategori dengan jumlah `done/total` dan tanda `(Direvisi)` jika kategori tersebut ada di `revision_history`, status foto `10/10`, dan **Inline Keyboard** dengan dua tombol: `Revisi Kategori` dan `Kirim Hasil` (aksi navigasi).
2. WHEN inspektor menekan `Revisi Kategori`, THE Bot SHALL menampilkan daftar 8 kategori sebagai **Inline Keyboard** (pemilihan kategori adalah navigasi).
3. WHEN inspektor memilih sebuah kategori untuk direvisi, THE Bot SHALL men-set `mode = "revisi"`, `revisi_kategori = {nama_kategori}`, dan menampilkan ulang seluruh komponen kategori tersebut satu per satu dengan jawaban sebelumnya tertera sebagai referensi serta **Reply Keyboard** berisi opsi jawaban valid plus tombol `Skip` untuk mempertahankan jawaban lama.
4. WHEN inspektor menekan `Skip` pada sebuah komponen di mode revisi, THE Bot SHALL tidak mengubah `answers[field]` dan melanjutkan ke komponen berikutnya.
5. WHEN inspektor memilih jawaban baru (dari Reply Keyboard) pada sebuah komponen di mode revisi, THE Bot SHALL menimpa `answers[field]` dengan nilai baru.
6. WHEN revisi kategori selesai, THE Bot SHALL selalu menambahkan entri `{nama_kategori: timestamp_iso8601}` ke `revision_history`, men-set `mode = "ringkasan"`, dan menampilkan ulang halaman Ringkasan dengan `ReplyKeyboardRemove` agar Reply Keyboard revisi hilang; tidak ada kondisi di mana revisi yang selesai tidak tercatat.
7. WHERE kategori yang direvisi adalah kategori 8 (Dokumen) dan jawaban `stnk` berubah, THE Bot SHALL menerapkan aturan pembersihan conditional dari Requirement 5 klausul 6 sebelum kembali ke Ringkasan.
8. THE Bot SHALL tidak mengizinkan revisi foto melalui flow ini; revisi foto dilakukan dengan `Foto Ulang` saat sesi foto aktif. (Catatan: jika kebutuhan revisi foto pasca-ringkasan muncul, akan diangkat sebagai requirement terpisah.)

### Requirement 8: Submit Hasil Inspeksi ke Frappe

**User Story:** Sebagai Inspector, saya ingin menekan satu tombol `Kirim Hasil` agar seluruh jawaban dan foto saya tersimpan ke Frappe sebagai dokumen Hasil Inspeksi, sehingga pekerjaan saya tercatat resmi.

#### Acceptance Criteria

1. WHEN inspektor menekan `Kirim Hasil` pada halaman Ringkasan, THE Bot SHALL memvalidasi bahwa seluruh 66 field komponen wajib pada `answers` terisi dan seluruh 10 entri `photos` memiliki `file_id`.
2. IF validasi pra-submit gagal, THEN THE Bot SHALL menampilkan pesan yang menyebutkan field/foto yang kosong dan kembali ke halaman Ringkasan tanpa memanggil Frappe.
3. WHEN validasi pra-submit lulus, THE Bot SHALL mengunduh setiap foto dari Telegram berdasarkan `file_id`, melakukan kompresi jika perlu (Requirement 6.8), lalu memanggil `POST /api/method/juragan.api.inspeksi.upload.upload_foto` untuk masing-masing foto secara serial dengan `filename = "{field_name}_{motor_tarikan}.jpg"` dan `doctype/docname` dikosongkan.
4. WHEN seluruh upload sukses, THE Bot SHALL menyusun payload `submit_hasil_inspeksi` berisi `motor_tarikan`, `telegram_id`, `tipe_inspeksi`, `komponen` (66 field + conditional STNK yang non-null), `foto_urls` (10 file_url), dan `catatan` (jika ada), lalu memanggil `POST /api/method/juragan.api.inspeksi.submit.submit_hasil_inspeksi` dengan header Authorization API Key.
5. WHEN response submit mengembalikan HTTP 200 dengan `message.ok = true`, THE Bot SHALL menghapus Inspection Session untuk motor tersebut dari Redis, menghapus `motor_tarikan` dari `pending_motors`, dan mengirim pesan konfirmasi ke inspektor berisi nama dokumen Hasil Inspeksi (mis. `HI-PJ-001-0001`).
6. WHEN konfirmasi sukses dikirim, IF `pending_motors` masih berisi minimal satu motor, THEN THE Bot SHALL menampilkan tombol `Lihat Daftar Motor` agar inspektor dapat lanjut ke motor berikutnya.
7. THE Bot SHALL menyertakan `Idempotency Key` (header HTTP atau field payload sesuai kontrak Frappe yang disepakati) yang konsisten antar retry sebuah submit yang sama, agar Frappe dapat menolak duplikasi. Jika Frappe belum mendukung idempotency key, THE Bot SHALL menggantungkan idempotensi pada validasi `status_inspeksi` Frappe (lihat klausul 9) dan tidak men-retry submit yang sudah pernah berhasil.
8. WHEN response submit gagal dengan HTTP 417/`ValidationError` yang menyebut payload tidak lengkap, THE Bot SHALL menampilkan pesan error ke inspektor dan kembali ke halaman Ringkasan tanpa menghapus session.
9. WHEN response submit gagal dengan HTTP 417/`ValidationError` yang menyebut status motor sudah `Selesai Inspeksi` (kemungkinan submit sebelumnya sudah berhasil meski Bot tidak menerima response), THE Bot SHALL memperlakukan sebagai sukses, menghapus session dari Redis, dan menampilkan pesan konfirmasi ke inspektor.
10. WHEN response submit gagal dengan HTTP 5xx atau network error, THE Bot SHALL melakukan retry maksimum 3 kali dengan exponential backoff 2s, 4s, 8s; IF seluruh retry gagal, THEN THE Bot SHALL menampilkan pesan `"Gagal mengirim ke server. Tekan Kirim Hasil lagi untuk mencoba ulang."` dan mempertahankan session.
11. THE Bot SHALL tidak menghapus session sebelum menerima konfirmasi sukses dari Frappe (kecuali kasus klausul 9).

### Requirement 9: Manajemen Session Redis

**User Story:** Sebagai Inspector, saya ingin progress saya tersimpan ketika koneksi terputus atau saya menutup Telegram, sehingga saya tidak perlu mengulang dari nol.

#### Acceptance Criteria

1. THE Bot SHALL menyimpan Inspection Session di Redis dengan key `session:{telegram_id}:{motor_tarikan}` dan TTL 86400 detik (24 jam) yang di-refresh setiap kali session di-update.
2. THE Bot SHALL menyimpan `pending_motors` di Redis dengan key `pending:{telegram_id}` dan TTL 86400 detik yang di-refresh setiap kali daftar di-update.
3. THE Bot SHALL menyimpan minimal field berikut sesuai PRD §8: `telegram_id`, `pending_motors`, `motor_id`, `inspection_started`, `mode`, `current_category`, `current_question`, `answers`, `stnk_answer`, `photo_index`, `photos`, `completed_categories`, `progress`, `revision_history`, `revisi_kategori`.
4. WHEN session sebuah motor di-update, THE Bot SHALL menyerialisasi seluruh field sebagai satu dokumen JSON tunggal di Redis (atau hash equivalent) sehingga read/write atomik per session.
5. IF Redis tidak dapat dihubungi pada saat operasi read/write session aktual dilakukan oleh handler, THEN THE Bot SHALL menampilkan pesan `"Sistem sedang sibuk, silakan coba lagi sebentar."` ke inspektor dan tidak melanjutkan handler. THE Bot SHALL tidak mengirim pesan sibuk secara proaktif ketika tidak ada operasi Redis yang sedang dicoba.
6. WHEN session expired (TTL habis) atau dihapus, IF inspektor mengirim callback yang merujuk ke session tersebut, THEN THE Bot SHALL menampilkan pesan `"Sesi inspeksi telah berakhir. Silakan ketik /mulai untuk memulai ulang."` dan mengabaikan callback.
7. THE Bot SHALL mendukung minimum 10 session aktif paralel pada satu instans bot dengan latency operasi Redis ≤ 50 ms p95.

### Requirement 10: Perintah dan Kontrol Bot

**User Story:** Sebagai Inspector, saya ingin perintah Telegram standar (`/start`, `/mulai`, `/bantuan`, `/status`) berperilaku konsisten, sehingga saya tahu cara menggunakan bot tanpa pelatihan formal.

#### Acceptance Criteria

1. WHEN inspektor mengirim `/start`, THE Bot SHALL menampilkan pesan sambutan, instruksi singkat, dan **Inline Keyboard** dengan tombol `Lihat Daftar Motor` (aksi navigasi). THE Bot SHALL tidak menampilkan Reply Keyboard pada layar awal agar tidak mengganggu interaksi pengguna baru.
2. WHEN inspektor mengirim `/mulai`, THE Bot SHALL menjalankan flow Daftar Motor (Requirement 3).
3. WHEN inspektor mengirim `/bantuan`, THE Bot SHALL menampilkan ringkasan perintah yang tersedia dan kontak admin (jika diset di env).
4. WHEN inspektor mengirim `/status`, THE Bot SHALL menampilkan: jumlah motor di `pending_motors`, motor aktif (jika ada), kategori sekarang, dan persentase kelengkapan `done/total` (66 + foto).
5. THE Bot SHALL tidak menyediakan perintah `/batal` selama `inspection_started = true`. WHILE `inspection_started = true`, THE Bot SHALL membalas perintah pembatalan dengan `"Inspeksi tidak dapat dibatalkan setelah dimulai. Hubungi admin jika perlu reset."`, termasuk untuk niat menghapus pemilihan motor aktif.
6. WHERE `inspection_started = false` (sebelum tombol `Mulai Inspeksi` ditekan), THE Bot SHALL menyediakan perintah `/batal` yang menghapus pemilihan motor aktif tanpa mengubah `pending_motors`.

### Requirement 11: Performa, Skalabilitas, dan Ketersediaan

**User Story:** Sebagai pemilik produk, saya ingin Bot responsif dan tersedia 24/7 untuk inspektor lapangan, sehingga inspeksi tidak tertunda karena masalah platform.

#### Acceptance Criteria

1. THE Bot SHALL merespons setiap input inspektor (kecuali aksi yang melibatkan upload foto ke Frappe) dalam waktu maksimum 2 detik p95 diukur dari penerimaan update Telegram hingga pengiriman pesan respons.
2. THE Bot SHALL mendukung minimum 10 Inspection Session aktif berjalan paralel pada satu instans tanpa kegagalan handler.
3. THE Bot SHALL memiliki target uptime minimum 99% per bulan kalender, diukur dari ketersediaan webhook endpoint dan kemampuan merespons polling Telegram.
4. WHEN call ke Frappe untuk `get_pending_list`, `upload_foto`, atau `submit_hasil_inspeksi` melebihi 30 detik (durasi > 30s, eksklusif), THE Bot SHALL membatalkan request, memperlakukan sebagai gagal, dan mengikuti aturan retry yang relevan (Requirement 8.10) atau menampilkan pesan sibuk. Call yang selesai pada tepat 30 detik SHALL dibiarkan selesai normal.
5. WHILE bot aktif, THE Bot SHALL meng-export endpoint `GET /healthz` yang mengembalikan HTTP 200 dengan body `{"status":"ok"}` jika koneksi Redis sehat, dan HTTP 503 jika tidak.

### Requirement 12: Keamanan dan Konfigurasi

**User Story:** Sebagai pemilik produk, saya ingin kredensial Frappe dan token Telegram tidak pernah masuk ke kode atau log, sehingga rotasi kredensial dan kepatuhan keamanan dapat dijaga.

#### Acceptance Criteria

1. THE Bot SHALL membaca `FRAPPE_URL`, `FRAPPE_API_KEY`, `FRAPPE_API_SECRET`, `TELEGRAM_BOT_TOKEN`, `REDIS_URL`, `REDIS_TTL`, `WEBHOOK_HOST`, dan `WEBHOOK_PORT` dari environment variable.
2. THE Bot SHALL gagal start (exit code non-zero) IF salah satu environment variable wajib di klausul 1 tidak terset, dengan pesan log yang menyebutkan nama variable yang hilang dan tidak menyebutkan nilai.
3. THE Bot SHALL tidak menulis nilai `FRAPPE_API_KEY`, `FRAPPE_API_SECRET`, atau `TELEGRAM_BOT_TOKEN` ke log pada level apapun.
4. THE Bot SHALL mengirim header `Authorization: token {FRAPPE_API_KEY}:{FRAPPE_API_SECRET}` pada setiap request ke Frappe REST API.
5. THE Bot SHALL berkomunikasi dengan Frappe via HTTPS WHERE `FRAPPE_URL` berskema `https://`; IF skema bukan `https://` di environment selain pengembangan lokal, THEN THE Bot SHALL log peringatan saat startup.
6. WHERE webhook endpoint diekspos, THE Bot SHALL mengabaikan request dari client yang tidak menyertakan header rahasia bersama (mis. `X-Inspection-Webhook-Secret` yang dikonfigurasi di env `WEBHOOK_SHARED_SECRET`); IF env tersebut tidak diset, THE Bot SHALL log peringatan saat startup namun tetap menerima request (untuk kompatibilitas tahap awal).

### Requirement 13: Audit Trail dan Observability

**User Story:** Sebagai pemilik produk, saya ingin setiap event penting tercatat dengan timestamp dan identitas inspektor, sehingga saya bisa menelusuri masalah dan memvalidasi kepatuhan SLA.

#### Acceptance Criteria

1. WHEN webhook `inspection_requested` diterima, THE Bot SHALL mencatat satu entri log structured berisi `event`, `motor_tarikan`, `inspector_chat_id`, `tipe_inspeksi`, dan `received_at` (ISO 8601).
2. WHEN inspektor memulai sesi inspeksi (`inspection_started` berubah dari false ke true), THE Bot SHALL mencatat `started_at`, `telegram_id`, dan `motor_tarikan`.
3. WHEN inspektor merevisi sebuah kategori, THE Bot SHALL mencatat entri ke `revision_history` di session dan menulis log structured dengan field `event_type = "CATEGORY_REVISED"`, `nama_kategori`, `telegram_id`, `motor_tarikan`, dan `timestamp`.
4. WHEN submit ke Frappe sukses, THE Bot SHALL mencatat `submitted_at`, `telegram_id`, `motor_tarikan`, `tipe_inspeksi`, `hasil_inspeksi_name`, dan durasi total sesi (selisih `started_at` ke `submitted_at`).
5. WHEN submit ke Frappe gagal, THE Bot SHALL mencatat status code, error message, dan attempt number.
6. THE Bot SHALL menulis log dalam format JSON satu baris per entri ke STDOUT agar dapat dikonsumsi container log driver.

### Requirement 14: Kontrak Data Komponen, Conditional STNK, dan Foto

**User Story:** Sebagai engineer, saya ingin kontrak field, opsi, dan urutan didefinisikan eksplisit, sehingga implementasi Bot dan Frappe konsisten.

#### Acceptance Criteria

1. THE Bot SHALL mengirim payload `komponen` dengan tepat 66 key wajib berikut: `kepala`, `sayap_dalam`, `sayap_luar`, `rangka_tengah`, `body_belakang`, `spakboard_depan`, `spakboard_belakang`, `leher_angsa`, `list_grafis`, `crankcase_assy`, `head_cylinder`, `cylinder`, `carburator_assy`, `oil_pump_assy`, `cover_crankcase_1`, `cover_crankcase_2`, `rantai_kamrat`, `crankshaft_assy`, `gear_rantai_vbelt`, `muffler_knalpot`, `fuel_tank`, `bahan_bakar`, `accu`, `cdi`, `kiprok`, `main_switch_steering_lock`, `ignition_coil`, `dinamo_stater`, `rotor_magnet`, `stator_kumparan`, `klakson`, `speedometer`, `lampu_depan`, `lampu_belakang`, `sein_depan`, `sein_belakang`, `shock_belakang`, `inner_tube_depan`, `master_cakram`, `plate_brake_shoe`, `piringan_rem_depan`, `master_cylinder_rem`, `kampas_cakram`, `kampas_tromol`, `tires_depan`, `tires_belakang`, `velg_cw_depan`, `velg_cw_belakang`, `velg_jarjari_depan`, `velg_jarjari_belakang`, `kaca_spion`, `tool_kit`, `tool_box`, `tutup_rantai_vbelt`, `panel_instrumen_kanan`, `panel_instrumen_kiri`, `jok_tempat_duduk`, `behel_belakang`, `foot_step_depan`, `foot_step_belakang`, `segitiga_atas`, `segitiga_bawah`, `kick_starter`, `pedal_gigi`, `pedal_rem`, `stnk`.
2. THE Bot SHALL mengirim setiap field di klausul 1 dengan nilai dari himpunan `{"Baik","Cukup","Rusak"}` kecuali `bahan_bakar` yang menggunakan himpunan `{"E","1/4","1/2","3/4","F"}`.
3. THE Bot SHALL menyertakan field conditional STNK dalam payload `komponen` HANYA WHEN field tersebut bernilai non-null setelah seluruh flow checklist+revisi: `stnk_hilang_polisi`, `stnk_tilang`, `stnk_ta` dengan himpunan `{"Ya","Tidak"}`; `stnk_mati_tanggal` dengan format `YYYY-MM-DD`.
4. THE Bot SHALL mengirim payload `foto_urls` dengan tepat 10 key berikut, dengan value berupa string `file_url` hasil `upload_foto`: `foto_tampak_depan`, `foto_tampak_belakang`, `foto_tampak_kanan`, `foto_tampak_kiri`, `foto_mesin`, `foto_nomor_rangka`, `foto_nomor_mesin`, `foto_stnk`, `foto_ban_depan`, `foto_ban_belakang`.
5. THE Bot SHALL mengirim `tipe_inspeksi` dengan nilai dari himpunan `{"Inspeksi","Inspeksi Ulang"}` sesuai `status_inspeksi` motor di response `get_pending_list` (Requirement 3.8). WHEN `tipe_inspeksi` di session tidak konsisten dengan `status_inspeksi` motor di response `get_pending_list` terbaru saat akan submit, THE Bot SHALL menolak submit, menampilkan pesan `"Status inspeksi motor telah berubah, silakan refresh daftar dan ulangi."`, dan tidak memanggil `submit_hasil_inspeksi`.

### Requirement 15: Penanganan Reassignment dan Pencabutan Tugas

**User Story:** Sebagai Admin, saya ingin perubahan penugasan motor (mengganti `telegram_inspector_id` atau menghapus motor) tercermin di Bot tanpa mengganggu inspektor yang tidak terkait.

#### Acceptance Criteria

1. WHEN inspektor membuka Daftar Motor (Requirement 3) dan `get_pending_list` tidak lagi mengembalikan sebuah motor yang masih ada di `pending_motors` Redis, THE Bot SHALL menghapus motor tersebut dari `pending_motors` dan tidak menampilkannya.
2. IF inspektor sudah memiliki Inspection Session aktif untuk motor yang ternyata sudah ditugaskan ulang ke inspektor lain (tidak muncul di response `get_pending_list` inspektor saat ini), THEN THE Bot SHALL menampilkan pesan `"Motor ini sudah dialihkan ke inspektor lain."` saat inspektor mencoba melanjutkan, dan menghapus session tersebut.
3. WHEN submit ke Frappe (Requirement 8) gagal dengan HTTP 403/PermissionError, THE Bot SHALL menampilkan pesan `"Akses ditolak untuk motor ini. Hubungi admin."` dan menghapus session lokal motor tersebut tanpa menghapus motor lain.
4. THE Bot SHALL tidak melakukan polling otomatis perubahan penugasan; sinkronisasi terjadi pada momen webhook diterima dan saat `/mulai` dipanggil.

### Requirement 16: Aturan UI Keyboard (Reply vs Inline)

**User Story:** Sebagai Inspector, saya ingin tombol pada bot konsisten dan tidak ambigu — input data yang sering diulang muncul sebagai keyboard di bawah, sedangkan navigasi/aksi muncul menempel pada pesan, sehingga saya tidak perlu mengetik bebas dan tidak bingung antara "memberi jawaban" dan "memilih aksi".

#### Acceptance Criteria

1. THE Bot SHALL menggunakan **Reply Keyboard** untuk seluruh input jawaban inspeksi yang memiliki himpunan nilai tertutup, yaitu: pilihan komponen `Baik/Cukup/Rusak`, pilihan `bahan_bakar` (`E`, `1/4`, `1/2`, `3/4`, `F`), pilihan conditional STNK Ya/Tidak/Skip, dan pilihan `Skip` pada `stnk_mati_tanggal`.
2. THE Bot SHALL menggunakan **Inline Keyboard** untuk seluruh aksi navigasi/transisi alur, yaitu: tombol `Lihat Daftar Motor` pada `/start` dan setelah submit, daftar motor pending pada `/mulai`, kartu konfirmasi `Mulai Inspeksi`, pilihan `Lanjutkan Sesi Sebelumnya` / `Mulai Ulang`, tombol `Konfirmasi` / `Foto Ulang` pada fase foto, tombol `Revisi Kategori` / `Kirim Hasil` pada Ringkasan, dan daftar kategori untuk dipilih pada flow Revisi.
3. THE Bot SHALL mengirim Reply Keyboard dengan parameter `resize_keyboard = true` dan `one_time_keyboard = true` agar keyboard menyesuaikan ukuran layar dan tertutup otomatis setelah ditekan satu kali.
4. WHEN sesi berpindah dari fase yang membutuhkan input pilihan (checklist, conditional STNK, revisi) ke fase yang tidak (foto, ringkasan, idle), THE Bot SHALL menyertakan `ReplyKeyboardRemove` pada pesan transisi sehingga Reply Keyboard sebelumnya hilang dari layar.
5. THE Bot SHALL tidak menggunakan Inline Keyboard untuk input jawaban komponen inspeksi, dan tidak menggunakan Reply Keyboard untuk aksi navigasi pemilihan motor, ringkasan, atau konfirmasi foto.
6. IF inspektor menggunakan client Telegram lama yang tidak menampilkan Reply Keyboard dengan benar dan mengetik nilai jawaban secara manual, THEN THE Bot SHALL tetap menerima teks tersebut WHERE teks adalah anggota set opsi valid untuk pertanyaan aktif, dan menolak dengan pesan klarifikasi WHERE teks bukan anggota set valid (sesuai Requirement 4.5).
7. THE Bot SHALL tidak menampilkan Reply Keyboard di layar `/start`, `/bantuan`, dan halaman Ringkasan; layar-layar tersebut hanya menggunakan Inline Keyboard atau tanpa keyboard sama sekali.

## Properti Korektness untuk Property-Based Testing

Daftar berikut adalah properti yang akan menjadi target property-based testing pada fase implementasi (kandidat untuk fase Design). Properti ini bersifat informatif untuk dokumen requirements, dan akan diformalkan pada `design.md`.

1. **Round-trip serialisasi session** — `deserialize(serialize(session)) == session` untuk semua kombinasi field session yang valid (Requirement 9).
2. **Invariant kelengkapan payload** — Untuk semua state internal yang lulus validasi pra-submit (Requirement 8.1), payload `komponen` yang dihasilkan berisi tepat 66 key wajib dan setiap value berasal dari himpunan opsi yang didefinisikan di Requirement 14.2.
3. **Invariant conditional STNK** — Untuk semua nilai `stnk ∈ {Baik,Cukup,Rusak}`, set field conditional yang muncul = subset yang ditentukan Requirement 5; setelah revisi `stnk`, tidak ada field conditional yang tidak relevan tertinggal di payload (Requirement 5.6).
4. **Idempotensi webhook** — Memproses payload webhook valid yang sama N kali menghasilkan `pending_motors` yang sama (sebagai set) dengan memprosesnya 1 kali (Requirement 1.8).
5. **Idempotensi submit** — Sukses submit berulang untuk `(telegram_id, motor_tarikan)` yang sama tidak menghasilkan dokumen Hasil Inspeksi duplikat di Frappe (Requirement 8.7, 8.9). Verifikasi via mock Frappe yang menegakkan unique constraint.
6. **Confluence revisi** — Untuk dua urutan revisi kategori berbeda yang berakhir pada set jawaban akhir yang sama, payload submit akhir identik (Requirement 7).
7. **Monotonicity progress** — `progress.done` tidak pernah menurun selama mode bukan `revisi`; pada mode revisi `done` tidak pernah melampaui `total` (Requirement 4, 7).
8. **Invariant pemilihan motor** — Motor yang tampil di Daftar Motor selalu subset dari response `get_pending_list` terbaru (Requirement 3.7, 15.1).
9. **Invariant tipe keyboard per fase** — Untuk semua state internal pada fase checklist/conditional/revisi, pesan yang dikirim Bot menggunakan Reply Keyboard; untuk fase pemilihan motor, ringkasan, dan konfirmasi foto, pesan menggunakan Inline Keyboard. Tidak pernah ada pesan checklist yang dikirim dengan Inline Keyboard sebagai input jawaban, dan tidak pernah ada pesan ringkasan yang menggunakan Reply Keyboard (Requirement 16).

## Catatan untuk Reviewer

- Inkonsistensi PRD vs API doc tentang jumlah komponen wajib (PRD: 65, API doc: 66) diselesaikan dengan mengikuti API doc (66 wajib + maks 4 conditional = maks 70 total). Lihat Requirement 14.1.
- Endpoint webhook Bot diasumsikan berjalan di port 8443 (atau sesuai env `WEBHOOK_PORT`) dengan TLS termination di reverse proxy.
- Idempotency key di sisi Frappe (Requirement 8.7) belum tertulis di API doc; jika Frappe tidak mendukung, implementasi mengandalkan pengecekan `status_inspeksi` (Requirement 8.9).
- Beberapa keputusan default yang diambil dan layak dikonfirmasi:
  - Frappe webhook bersifat fire-and-forget tanpa retry; Bot tidak meminta retry dari Frappe.
  - Pada `Inspeksi Ulang`, Bot memulai checklist dari kosong (tidak prefill jawaban inspeksi sebelumnya).
  - `/batal` hanya tersedia sebelum `Mulai Inspeksi` ditekan.
  - Kompresi foto dilakukan otomatis sisi-bot ketika ukuran > 5 MB (downscale + JPEG quality reduction); tidak ada threshold di bawah 5 MB.
  - Tidak ada perintah admin di sisi Bot — pengelolaan inspektor dan penugasan dilakukan eksklusif di Frappe.
