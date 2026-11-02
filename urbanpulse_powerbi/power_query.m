// ============================================================================
// UrbanPulse — Power Query (M)
//
// SEBELUM MULAI: buat parameter
//   Home > Transform data > Manage Parameters > New Parameter
//   Name: DataFolder | Type: Text | Current Value: C:\...\urbanpulse_powerbi\data\
//   (akhiri path dengan backslash "\")
//
// Tiap blok bertanda QUERY (judul diapit tanda sama dengan) adalah SATU query terpisah:
//   Home > New Source > Blank Query > Advanced Editor > tempel isi blok (mulai dari "let").
//   Beri nama query PERSIS seperti nama pada judul blok (nama dipakai oleh DAX dan query lain).
//
// Semua timestamp sudah WIB (+07); skrip memakai 19 karakter pertama sebagai waktu lokal,
// sehingga tidak ada konversi zona waktu.
// ============================================================================


// ===== QUERY: dim_location =====
let
    Source   = Csv.Document(File.Contents(DataFolder & "gold_dim_location.csv"),
                            [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Selected = Table.SelectColumns(Promoted, {"location_id", "location_name", "area", "latitude", "longitude"}),
    Typed    = Table.TransformColumnTypes(Selected,
                   {{"location_id", type text}, {"location_name", type text}, {"area", type text},
                    {"latitude", type number}, {"longitude", type number}}, "en-US")
in
    Typed


// ===== QUERY: fact_traffic_hourly =====
// Sumber: layer Gold gold_weather_traffic_hourly (traffic per jam + kolom cuaca)
let
    Source   = Csv.Document(File.Contents(DataFolder & "gold_weather_traffic_hourly.csv"),
                            [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    AddTs    = Table.AddColumn(Promoted, "hour_ts_wib",
                   each DateTime.FromText(Text.Start([hour_ts], 19), [Format = "yyyy-MM-dd HH:mm:ss", Culture = "en-US"]),
                   type datetime),
    Selected = Table.SelectColumns(AddTs,
                   {"location_id", "hour_ts_wib", "obs_date", "hour_of_day", "avg_speed", "min_speed",
                    "avg_congestion_ratio", "max_congestion_ratio", "observation_count",
                    "temperature", "humidity", "precipitation", "wind_speed", "cloud_cover"}),
    Renamed  = Table.RenameColumns(Selected, {{"hour_ts_wib", "hour_ts"}}),
    Typed    = Table.TransformColumnTypes(Renamed,
                   {{"location_id", type text}, {"obs_date", type date}, {"hour_of_day", Int64.Type},
                    {"avg_speed", type number}, {"min_speed", type number},
                    {"avg_congestion_ratio", type number}, {"max_congestion_ratio", type number},
                    {"observation_count", Int64.Type}, {"temperature", type number}, {"humidity", type number},
                    {"precipitation", type number}, {"wind_speed", type number}, {"cloud_cover", type number}}, "en-US")
in
    Typed


// ===== QUERY: fact_traffic_30min =====
// Sumber: silver_traffic_enriched.csv (hasil olah Python: congestion ratio + flag anomali)
let
    Source   = Csv.Document(File.Contents(DataFolder & "silver_traffic_enriched.csv"),
                            [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Selected = Table.SelectColumns(Promoted,
                   {"location_id", "frc", "slot", "hour_ts", "date", "hour_of_day", "minute",
                    "current_speed", "free_flow_speed", "current_travel_time", "free_flow_travel_time",
                    "congestion_ratio", "speed_z", "speed_anomaly", "cong_z", "cong_iqr_upper",
                    "cong_anom_z", "cong_anom_iqr", "cong_spike", "cong_status", "ingestion_mode"}),
    Typed    = Table.TransformColumnTypes(Selected,
                   {{"location_id", type text}, {"frc", type text}, {"slot", type datetime}, {"hour_ts", type datetime},
                    {"date", type date}, {"hour_of_day", Int64.Type}, {"minute", Int64.Type},
                    {"current_speed", type number}, {"free_flow_speed", type number},
                    {"current_travel_time", type number}, {"free_flow_travel_time", type number},
                    {"congestion_ratio", type number}, {"speed_z", type number}, {"speed_anomaly", Int64.Type},
                    {"cong_z", type number}, {"cong_iqr_upper", type number},
                    {"cong_anom_z", Int64.Type}, {"cong_anom_iqr", Int64.Type}, {"cong_spike", Int64.Type},
                    {"cong_status", type text}, {"ingestion_mode", type text}}, "en-US")
in
    Typed


// ===== QUERY: fact_weather_hourly =====
// Cuaca antarruas hampir identik, jadi 10 ruas dirangkum menjadi satu deret cuaca kota per jam.
let
    Source   = Csv.Document(File.Contents(DataFolder & "silver_weather_hourly.csv"),
                            [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    AddTs    = Table.AddColumn(Promoted, "hour_ts_wib",
                   each DateTime.FromText(Text.Start([hour_ts], 19), [Format = "yyyy-MM-dd HH:mm:ss", Culture = "en-US"]),
                   type datetime),
    Selected = Table.SelectColumns(AddTs,
                   {"hour_ts_wib", "temperature", "humidity", "precipitation", "wind_speed", "cloud_cover", "ingestion_mode"}),
    Typed    = Table.TransformColumnTypes(Selected,
                   {{"temperature", type number}, {"humidity", type number}, {"precipitation", type number},
                    {"wind_speed", type number}, {"cloud_cover", type number}, {"ingestion_mode", type text}}, "en-US"),
    Grouped  = Table.Group(Typed, {"hour_ts_wib"},
                   {{"temperature", each List.Average([temperature]), type number},
                    {"humidity", each List.Average([humidity]), type number},
                    {"wind_speed", each List.Average([wind_speed]), type number},
                    {"cloud_cover", each List.Average([cloud_cover]), type number},
                    {"precip_max", each List.Max([precipitation]), type number},
                    {"live_rows", each List.Count(List.Select([ingestion_mode], each _ = "live")), Int64.Type}}),
    Renamed  = Table.RenameColumns(Grouped, {{"hour_ts_wib", "hour_ts"}}),
    AddRain  = Table.AddColumn(Renamed, "is_rain", each if [precip_max] > 0 then 1 else 0, Int64.Type),
    AddLive  = Table.AddColumn(AddRain, "is_live", each if [live_rows] > 0 then 1 else 0, Int64.Type),
    AddDate  = Table.AddColumn(AddLive, "date", each DateTime.Date([hour_ts]), type date),
    Sorted   = Table.Sort(AddDate, {{"hour_ts", Order.Ascending}})
in
    Sorted


// ===== QUERY: dim_hour =====
// Dimensi jam (cakupan penuh cuaca); dipakai slicer waktu bersama untuk traffic gold, silver, dan cuaca.
let
    Source  = Table.Distinct(Table.SelectColumns(fact_weather_hourly, {"hour_ts"})),
    AddDate = Table.AddColumn(Source, "date", each DateTime.Date([hour_ts]), type date),
    AddHour = Table.AddColumn(AddDate, "hour_of_day", each Time.Hour(DateTime.Time([hour_ts])), Int64.Type),
    Sorted  = Table.Sort(AddHour, {{"hour_ts", Order.Ascending}})
in
    Sorted
