# UrbanPulse — Proyek Power BI (.pbip)

Proyek berbasis teks yang dibuka di **Power BI Desktop**, lalu disimpan sebagai `.pbix` (File > Save as).

## Yang perlu diaktifkan (sekali)
File > Options and settings > Options > **Preview features**, aktifkan:
- **Power BI Project (.pbip) save option**
- **Store semantic model using TMDL format**
- **Store reports using enhanced metadata format (PBIR)**

Restart Power BI Desktop setelah mengaktifkan.

## Cara membuka
1. Ekstrak folder `UrbanPulse` ke lokasi tetap (misalnya `C:\UrbanPulse\`).
2. **Atur path data.** Parameter `DataFolder` berisi path placeholder `C:\UrbanPulse\data\`. Jika folder kamu berbeda, ubah lewat salah satu cara:
   - edit baris pertama `UrbanPulse.SemanticModel\definition\expressions.tmdl` (akhiri path dengan `\`), atau
   - setelah dibuka: Home > Transform data > Edit parameters.
3. Buka `UrbanPulse.pbip`, lalu Home > **Refresh**.
4. File > Save as untuk menyimpan `.pbix`.

## Isi proyek
- **Model:** 5 tabel (`dim_location`, `dim_hour`, `fact_traffic_hourly`, `fact_traffic_30min`, `fact_weather_hourly`), tabel `_Measures`, 5 relasi star-schema, dan **38 measure DAX**. Flag anomali berasal dari `silver_traffic_enriched.csv` (dihitung di Python).
- **Laporan:** 6 halaman (Ringkasan, Waktu & Tren, Ruas & Area, Hubungan & Anomali, Cuaca, Catatan Data), 52 visual. Slicer area, ruas, dan waktu disinkronkan di halaman 1–4; halaman Cuaca tanpa slicer.
- `urbanpulse_theme.json`: tema warna; impor lewat View > Themes > Browse for themes.
- `data/`: 4 CSV sumber.

## Status pengujian
- File JSON laporan (64 file) **lolos validasi skema resmi Microsoft** (PBIR, pbip, pbir, pbism).
- Setiap kolom dan measure yang dipakai visual sudah dicek ada di model.
- **Model TMDL, M, dan DAX belum pernah dibuka di Power BI Desktop** (tidak tersedia di lingkungan pembuatnya). Jika Desktop menampilkan error saat memuat, kirim pesan error-nya apa adanya.

## Yang perlu dirapikan manual setelah terbuka
- **Heatmap:** pada kedua matrix (halaman 2), terapkan Cell elements > Background color > gradient (min `#FFF7E6`, tengah `#FDBB84`, maks `#7F0000`).
- **Warna seri** pada scatter Q8 mengikuti tema; atur manual bila perlu.
- Tata letak diatur untuk kanvas 1280×720; geser seperlunya.
- Tema `urbanpulse_theme.json` belum terpasang otomatis; impor sendiri.

## Validasi angka
Bandingkan hasil dengan tabel acuan di `README_PowerBI.md` (paket build sebelumnya), misalnya: `Spikes` = 8, `Speed Anomalies` = 1, `Rain Hours` = 94, `Speed vs Travel Time (r)` = −0,187 (tanpa filter).
