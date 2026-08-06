# Rencana Implementasi: Integrasi & Pengayaan Data Gaji (Salary Data)

Rencana ini merinci langkah-langkah untuk memperbaiki dan meningkatkan ekstraksi data gaji (salary) dari situs lowongan kerja, memperkaya database local `jobs_data.json` dengan regex heuristik yang lebih kuat, serta melakukan sinkronisasi ke database Supabase.

---

## Masalah & Analisis
1. **Pola Regex Kaku**: Pola pencarian currency saat ini mengabaikan simbol Rp yang ditulis dengan dot (seperti `Rp.`) yang merupakan cara penulisan umum di Indonesia.
2. **Kekosongan Data Gaji Historis**: Kebanyakan dari 4700+ lowongan kerja yang sudah ada di database saat ini tidak memiliki kolom `salary` karena data dikumpulkan sebelum fitur ini ada.
3. **Pengayaan Deskripsi**: Masih ada peluang mengekstrak salary secara lebih optimal dari deskripsi lowongan kerja yang ada jika menggunakan pola regex yang lebih fleksibel.

---

## Usulan Perubahan

### 1. Perbaikan Regex Ekstraksi Gaji

#### [MODIFY] [scraper.py](file:///home/lightman/Documents/Project/scraper.py)
* Perbarui method `extract_salary` untuk mendeteksi penulisan currency dengan dot opsional setelah simbol (contoh: `Rp.`, `IDR.`, `SGD.`).
* Tambahkan dukungan untuk currency internasional yang umum (USD, SGD, EUR, AUD).

#### [MODIFY] [scratch/extract_salaries_from_desc.py](file:///home/lightman/Documents/Project/scratch/extract_salaries_from_desc.py)
* Lakukan penyesuaian pola regex yang sama untuk mendeteksi gaji di deskripsi teks.
* Tambahkan pendeteksian pola rentang gaji tanpa simbol currency jika ada kata kunci penjelas (misal: "Gaji 5 - 10 juta").

### 2. Proses Sinkronisasi & Pengayaan Database
* Jalankan `extract_salaries_from_desc.py` pada seluruh data di `jobs_data.json`.
* Jalankan `upload_to_supabase.py` untuk mengunggah dan meng-update kolom `salary` dari lowongan-lowongan kerja yang berhasil diperkaya.

---

## Rencana Verifikasi

### Pengujian Manual / Script
1. **Regex Testing**: Jalankan potongan regex pada format teks input seperti `Rp. 10.000.000` dan `Gaji: 5 juta - 7 juta` untuk memastikan kecocokan.
2. **Execution Check**: Jalankan script inspeksi database untuk memverifikasi kenaikan jumlah entri lowongan kerja yang memiliki info gaji non-null.
3. **Frontend Preview**: Jalankan `npm run dev` pada Next.js frontend, lalu periksa halaman Jobs untuk memastikan badge gaji (💰) tampil rapi dan benar.
