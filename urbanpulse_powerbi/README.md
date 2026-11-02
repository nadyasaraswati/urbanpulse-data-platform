# UrbanPulse — Paket Build Power BI

Paket ini berisi semua yang dibutuhkan untuk merakit dashboard Power BI Desktop (±30–45 menit): data, Power Query (M), measure DAX, tema, dan spesifikasi visual per halaman. File `.pbix` tidak disertakan (format biner, dirakit di Power BI Desktop).

> **Status pengujian:** CSV, flag anomali, dan angka acuan dihitung dan diverifikasi di Python. Skrip M dan DAX **belum dijalankan di Power BI Desktop** (tidak tersedia di lingkungan pembuatnya). Gunakan bagian **6. Validasi** untuk memastikan hasilnya sama; jika ada selisih, perbaiki sesuai pesan error Power BI.

## 1. Isi paket

| File | Fungsi |
|---|---|
| `data/silver_traffic_enriched.csv` | Silver 30 menit + `congestion_ratio` + flag anomali (dihitung di Python) |
| `data/gold_weather_traffic_hourly.csv` | Gold: traffic per jam + kolom cuaca |
| `data/gold_dim_location.csv` | Dimensi ruas (nama, area, koordinat) |
| `data/silver_weather_hourly.csv` | Cuaca per jam per ruas (dirangkum jadi deret kota di Power Query) |
| `power_query.m` | 5 query M + parameter `DataFolder` |
| `measures.dax` | Measure DAX (KPI, ranking, korelasi, anomali, cuaca) |
| `urbanpulse_theme.json` | Tema warna/font (View > Themes > Browse for themes) |

## 2. Langkah merakit

1. **Parameter:** Home > Transform data > Manage Parameters > New. Name `DataFolder`, Type Text, isi path folder `data\` (akhiri dengan `\`).
2. **Query:** untuk tiap blok `===== QUERY: nama =====` di `power_query.m`: New Source > Blank Query > Advanced Editor > tempel > namai persis (`dim_location`, `fact_traffic_hourly`, `fact_traffic_30min`, `fact_weather_hourly`, `dim_hour`). Close & Apply.
3. **Relasi** (Model view; semuanya One-to-many, arah filter Single):

   | Dari (1) | Ke (banyak) |
   |---|---|
   | `dim_location[location_id]` | `fact_traffic_hourly[location_id]` |
   | `dim_location[location_id]` | `fact_traffic_30min[location_id]` |
   | `dim_hour[hour_ts]` | `fact_traffic_hourly[hour_ts]` |
   | `dim_hour[hour_ts]` | `fact_traffic_30min[hour_ts]` |
   | `dim_hour[hour_ts]` | `fact_weather_hourly[hour_ts]` |

4. **Measure:** Enter data > tabel kosong `_Measures` > tempel measure dari `measures.dax` satu per satu; atur format sesuai komentar `// fmt:`.
5. **Pengurutan:** pada `dim_location`, urutkan `location_name` menurut `location_id` bila diinginkan.
6. **Tema:** View > Themes > Browse for themes > `urbanpulse_theme.json`.
7. **Halaman & visual:** ikuti bagian 5. Slicer disinkronkan ke halaman 1–4 (View > Sync slicers); **halaman Cuaca tanpa slicer**.

## 3. Aturan model

- Waktu semuanya WIB; tidak ada konversi zona waktu.
- Slicer waktu memakai `dim_hour[hour_ts]` (Between) sehingga memfilter traffic gold, silver, dan cuaca sekaligus.
- Gunakan kolom `slot`, `current_speed`, `current_travel_time`, `congestion_ratio` pada scatter dengan **Don't summarize** supaya tiap titik adalah satu slot.

## 4. Definisi

| Istilah | Definisi |
|---|---|
| `congestion_ratio` | `max(0, 1 − current_speed / free_flow_speed)` |
| Porsi macet (`Congested Share`) | Porsi ruas-jam dengan `avg_congestion_ratio > 0`. **Bukan** ambang "macet" yang divalidasi |
| `Speed CV` | Std kecepatan per jam / rata-rata kecepatan (per ruas); lebih kecil = lebih stabil |
| `cong_z` | Z-score congestion per ruas: `(c − rata-rata ruas) / std ruas` (std sampel); kosong jika std = 0 |
| `cong_iqr_upper` | Fence per ruas: `Q3 + 1,5 × IQR` (kuantil interpolasi linear) |
| `cong_anom_z` / `cong_anom_iqr` | `cong_z > 3` / `congestion_ratio > cong_iqr_upper` |
| **`cong_spike` (Lonjakan)** | `cong_anom_z = 1` **dan** `cong_anom_iqr = 1`. Ukuran utama di dashboard |
| `speed_anomaly` | `abs(speed_z) > 3`, Z-score global kecepatan seluruh slot |

Catatan interpretasi: flag dihitung sekali pada **seluruh** data di Python dan tidak berubah saat slicer dipakai (filter hanya mengubah yang ditampilkan). Distribusi congestion sangat miring (mayoritas 0): 9 dari 10 ruas punya IQR = 0, sehingga fence IQR = 0 dan semua nilai > 0 ikut terflag. Karena itu `IQR Anomalies` jauh lebih banyak daripada `Z Anomalies`, dan lonjakan yang dilaporkan adalah irisan keduanya.

## 5. Spesifikasi halaman

Slicer (halaman 1–4): `dim_location[area]`, `dim_location[location_name]`, `dim_hour[hour_ts]` (Between).

**Halaman 1 — Ringkasan**
- Text box banner batasan data: traffic hanya ±20 jam, seluruhnya akhir pekan, tanpa hujan; perbandingan hari kerja vs akhir pekan dan cuaca vs kecepatan belum dapat dianalisis.
- 6 card: `Top Congested Road`, `Peak Hour Label`, `Avg Congestion`, `Avg Speed`, `Most Stable Road`, `Spikes`.
- **Q1** Bar chart horizontal: Y `location_name`, X `Avg Congestion`, Legend `dim_location[area]`, urut menurun, data label.
- **Q2** Clustered column and line chart: X `fact_traffic_hourly[hour_of_day]`, Column `Avg Congestion`, Line (sumbu sekunder) `Congested Share`.

**Halaman 2 — Waktu & Tren**
- **Q2** Matrix heatmap: Rows `location_name`, Columns `fact_traffic_hourly[hour_of_day]`, Values `Avg Congestion`; Conditional formatting > Background color > gradient (min `#FFF7E6`, tengah `#FDBB84`, maks `#7F0000`).
- **Q6** Line chart: X `fact_traffic_30min[slot]` (sumbu kontinu, bukan hierarki tanggal), Y `Avg Congestion 30m` dan `Congestion MA (±30 min)`; sumbu sekunder `Speed MA (±30 min)`.
- **Q6** Matrix: Rows `location_name`, Columns `slot`, Values `Avg Congestion 30m` dengan gradient yang sama.
- **Q6** Line chart: X `slot`, Y `Avg Speed 30m`, Legend `location_name`.

**Halaman 3 — Ruas & Area**
- **Q5** Bar chart: Y `location_name`, X `Speed CV`, urut menaik (paling stabil di atas).
- **Q7** Clustered column: X `dim_location[area]`, Y `Avg Congestion`; di sebelahnya table: `area`, `Avg Congestion`, `Congestion Median`, `Max Congestion`, `Congested Share`, `Avg Speed`.
- **Q9** Table per ruas: `location_name`, `Road-Hours`, `Avg Congestion`, `Congestion Std`, `Congestion Median`, `Congestion P90`, `Max Congestion`, `Zero Share`; urut menurut `Congestion Std`. (Opsional: custom visual "Box and Whisker chart" dari AppSource.)

**Halaman 4 — Hubungan & Anomali**
- **Q8** Scatter: X `current_speed`, Y `current_travel_time` (Don't summarize), Details `slot`, Legend `location_name`. Card `Speed vs Travel Time (r)` dan table per ruas: `location_name`, `Speed vs Travel Time (r)`, `Slots`. Waktu tempuh bergantung pada panjang ruas, jadi korelasi dalam ruas lebih informatif daripada korelasi gabungan.
- **Q10** Scatter: X `slot`, Y `congestion_ratio` (Don't summarize), Details `location_name`, Legend `cong_status` (Normal = abu-abu transparan, Lonjakan = merah, titik besar).
- 5 card: `Spikes`, `Spike Share`, `Speed Anomalies`, `Z Anomalies`, `IQR Anomalies`.
- Table lonjakan: `slot`, `location_name`, `area`, `current_speed`, `free_flow_speed`, `congestion_ratio`, `cong_z`; visual filter `cong_spike = 1`, Top N 15 menurut `congestion_ratio`.

**Halaman 5 — Cuaca** (tanpa slicer; seluruh periode 1 Okt–1 Nov 2026)
- 5 card: `Avg Temp`, `Rain Hours`, `Rain Days`, `Max Rain`, `Live Hours`.
- Combo: X `dim_hour[hour_ts]`, Line `Avg Temp`, Column (sekunder) `Max Rain`.
- Column: X `dim_hour[date]`, Y `Rain Hours`.
- Line: X `dim_hour[hour_of_day]`, Y `Avg Temp`.

**Halaman 6 — Catatan Data**
- Text box definisi (bagian 4), dan card `Traffic Period`, `Weekend Share`, `Rain Rows in Traffic Period`, `Observed Hours`.
- Catat bahwa sumber API cuaca belum dikonfirmasi (diduga reanalysis ERA5), dan 20 jam terakhir bermode `live`.

## 6. Validasi (angka acuan dari Python; tanpa filter apa pun)

**Global**

| metrik | nilai | catatan |
|---|---|---|
| Baris fact_traffic_hourly | 200 |  |
| Baris fact_traffic_30min | 370 |  |
| Jumlah ruas | 10 |  |
| Observed Hours (jam unik gold) | 20 |  |
| Avg Congestion (semua ruas-jam) | 0.0231 |  |
| Avg Speed (km/jam) | 32.65 |  |
| Congested Share (avg_congestion > 0) | 0.175 |  |
| Zero Share | 0.825 |  |
| Peak Hour | 15 | rata-rata 0.0985 |
| Cong anomali Z (cong_anom_z) | 8 |  |
| Cong anomali IQR (cong_anom_iqr) | 33 |  |
| Spikes (Z & IQR) | 8 |  |
| Speed Anomalies (abs(Z) > 3, global) | 1 |  |
| Pooled r speed vs travel time | -0.1872 |  |
| Weather Hours | 761 |  |
| Rain Hours (precip_max > 0) | 94 |  |
| Rain Days | 14 |  |
| Live Hours | 20 |  |
| Avg Temp (°C) | 30.16 |  |
| Max Rain (mm/jam) | 2.3 |  |

**Per ruas** (`Rank Stability` imbang untuk ruas dengan CV = 0; `Most Stable Road` menampilkan semua yang imbang; kolom `pearson_30m` kosong untuk ruas tanpa variasi)

| location_name | avg_cong | max_cong | speed_mean | speed_std | speed_cv | rank_cong | rank_stability | pearson_30m | spikes | speed_anom |
|---|---|---|---|---|---|---|---|---|---|---|
| Basuki Rahmat | 0.0565 | 0.2857 | 32.3 | 2.5204 | 0.078 | 1 | 6 | -0.9973 | 0 | 0 |
| Mayjen Sungkono | 0.0389 | 0.2903 | 28.95 | 2.2763 | 0.0786 | 2 | 8 | -0.9986 | 0 | 0 |
| HR Muhammad | 0.0377 | 0.2368 | 37.45 | 2.6651 | 0.0712 | 3 | 5 | -0.9996 | 0 | 0 |
| Tunjungan | 0.0307 | 0.3103 | 29.0 | 2.3283 | 0.0803 | 4 | 9 | -0.9933 | 1 | 1 |
| Ahmad Yani | 0.0292 | 0.275 | 38.85 | 3.044 | 0.0784 | 5 | 7 | -0.9988 | 2 | 0 |
| Darmo | 0.0176 | 0.2353 | 33.4 | 1.7592 | 0.0527 | 6 | 4 | -0.9994 | 2 | 0 |
| Kertajaya | 0.012 | 0.1562 | 29.75 | 0.6177 | 0.0208 | 7 | 2 | -0.9999 | 1 | 0 |
| Gubeng | 0.0081 | 0.1935 | 30.75 | 0.7864 | 0.0256 | 8 | 3 | -0.9993 | 2 | 0 |
| Kenjeran | 0.0 | 0.0 | 33.0 | 0.0 | 0.0 | 9 | 1 |  | 0 | 0 |
| Rajawali | 0.0 | 0.0 | 33.0 | 0.0 | 0.0 | 9 | 1 |  | 0 | 0 |

**Per area**

| area | avg_cong | avg_speed | road_hours |
|---|---|---|---|
| Barat | 0.0383 | 33.2 | 40 |
| Pusat | 0.0436 | 30.65 | 40 |
| Selatan | 0.0234 | 36.125 | 40 |
| Timur | 0.01 | 30.25 | 40 |
| Utara | 0.0 | 33.0 | 40 |

Toleransi: selisih pembulatan di digit terakhir bisa terjadi. Jika `Spikes` bukan 8 atau `Speed vs Travel Time (r)` bukan −0,187, periksa tipe kolom `fact_traffic_30min` (flag harus Whole number).

## 7. Batasan data

- Traffic ±20 jam (31 Okt 21:00 – 1 Nov 16:30), seluruhnya akhir pekan; tidak ada hujan pada periode traffic. Pola jam bersifat deskriptif karena tiap jam hanya muncul sekali.
- Tiga observasi awal tiap ruas berjarak 1 jam, setelah itu 30 menit; slot hilang tampak sebagai sel kosong pada matrix.
- Cuaca antarruas hampir identik; deret kota dibentuk dengan rata-rata (suhu, kelembapan, angin, awan) dan maksimum (curah hujan).
- Hasil ML (Part B notebook) tidak termasuk dalam dashboard ini.
