"""UrbanPulse — Traffic & Weather Analytics (Streamlit).

Jalankan:  streamlit run app.py
Data    :  letakkan 5 file CSV di folder `data/` (di sebelah app.py), atau unggah lewat sidebar.
"""
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

st.set_page_config(page_title="UrbanPulse — Traffic & Weather Analytics", page_icon="🚦", layout="wide")

TZ = "Asia/Jakarta"
Z_THRESH, IQR_K = 3.0, 1.5
REQUIRED = [
    "gold_dim_location.csv",
    "gold_weather_traffic_hourly.csv",
    "silver_traffic_30min.csv",
    "silver_weather_hourly.csv",
]
SILVER_COLS = ["location_id", "observation_ts", "current_speed", "free_flow_speed",
               "current_travel_time", "free_flow_travel_time", "frc"]
PAL = ["#2563eb", "#e4572e", "#17a398", "#f2a900", "#8e44ad", "#d6336c", "#4c9f38", "#00a6d6", "#a0522d", "#6c757d"]
AREA_PAL = ["#2563eb", "#e4572e", "#17a398", "#f2a900", "#8e44ad"]
SCALE = [[0, "#fff7e6"], [0.35, "#fdbb84"], [0.7, "#e34a33"], [1, "#7f0000"]]


# ───────────────────────── util tampilan ─────────────────────────
def show(fig, key):
    fig.update_layout(margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=-0.2))
    try:
        st.plotly_chart(fig, width="stretch", key=key)
    except TypeError:                       # Streamlit versi lama
        st.plotly_chart(fig, use_container_width=True, key=key)


def show_df(df, key=None):
    try:
        st.dataframe(df, hide_index=True, width="stretch", key=key)
    except TypeError:
        st.dataframe(df, hide_index=True, use_container_width=True, key=key)


def metric(col, label, value, sub=""):
    col.metric(label, value)
    if sub:
        col.caption(sub)


def area_legend():
    st.markdown("Warna batang = area: " + " &nbsp; ".join(
        f"<span style='color:{ACOL[a]}'>●</span> {a}" for a in AREAS), unsafe_allow_html=True)


def fmt(x, d=3):
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}f}"


def pct(x):
    return "–" if x is None or np.isnan(x) else f"{x * 100:.1f}%"


# ───────────────────────── sumber data ─────────────────────────
@st.cache_data(show_spinner=False)
def read_dir(path: str) -> dict:
    return {n: (Path(path) / n).read_bytes() for n in REQUIRED if (Path(path) / n).exists()}


def locate_files() -> dict:
    here = Path(__file__).parent
    for cand in (here / "data", here):
        found = read_dir(str(cand))
        if len(found) == len(REQUIRED):
            return found
    return {}


files = locate_files()
if not files:
    st.sidebar.warning("File CSV belum ditemukan di folder `data/`. Unggah 4 file di bawah ini.")
    ups = st.sidebar.file_uploader("Unggah CSV", type="csv", accept_multiple_files=True)
    files = {u.name: u.getvalue() for u in ups or [] if u.name in REQUIRED}
    missing = [n for n in REQUIRED if n not in files]
    if missing:
        st.title("UrbanPulse — Traffic & Weather Analytics")
        st.info("Letakkan file berikut di folder `data/` di sebelah `app.py`, atau unggah lewat sidebar:\n\n"
                + "\n".join(f"- `{n}`" for n in missing))
        st.stop()


# ───────────────────────── pemrosesan ─────────────────────────
@st.cache_data(show_spinner="Memuat data…")
def build(file_items: tuple):
    raw = dict(file_items)

    def ts(s):   # waktu WIB tanpa zona (wall-clock) untuk tampilan
        return pd.to_datetime(s, utc=True).dt.tz_convert(TZ).dt.tz_localize(None)

    dim = pd.read_csv(BytesIO(raw["gold_dim_location.csv"]))[["location_id", "location_name", "area"]]
    g = pd.read_csv(BytesIO(raw["gold_weather_traffic_hourly.csv"]))
    g["t"] = ts(g["hour_ts"])
    g = g.merge(dim, on="location_id", how="left")
    g = g.rename(columns={"location_name": "ruas", "avg_speed": "speed", "avg_congestion_ratio": "cong",
                          "max_congestion_ratio": "cong_max", "hour_of_day": "hour"})

    s = pd.read_csv(BytesIO(raw["silver_traffic_30min.csv"]), usecols=SILVER_COLS)
    s["t"] = ts(s["observation_ts"]).dt.floor("30min")
    s = s.merge(dim, on="location_id", how="left").rename(columns={"location_name": "ruas"})
    s["cong"] = (1 - s["current_speed"] / s["free_flow_speed"]).clip(lower=0)
    # anomali (logika sama dengan notebook), dihitung pada seluruh data
    z_speed = (s["current_speed"] - s["current_speed"].mean()) / s["current_speed"].std()
    s["speed_anom"] = z_speed.abs() > Z_THRESH
    gr = s.groupby("location_id")["cong"]
    s["cong_z"] = (s["cong"] - gr.transform("mean")) / gr.transform("std").replace(0, np.nan)
    q1, q3 = gr.transform(lambda x: x.quantile(.25)), gr.transform(lambda x: x.quantile(.75))
    s["spike"] = (s["cong_z"] > Z_THRESH) & (s["cong"] > q3 + IQR_K * (q3 - q1))

    w = pd.read_csv(BytesIO(raw["silver_weather_hourly.csv"]))
    w["t"] = ts(w["hour_ts"])
    wc = (w.groupby("t").agg(temp=("temperature", "mean"), rain=("precipitation", "max"),
                             live=("ingestion_mode", lambda x: (x == "live").any()))
            .reset_index().sort_values("t"))
    meta = {"n_weather_rows": len(w), "n_weather_loc": w["location_id"].nunique()}
    return dim, g, s, wc, meta


dim, G, S, WC, META = build(tuple(sorted(files.items())))
LOCS = dim.sort_values("location_id")["location_name"].tolist()
AREAS = sorted(dim["area"].unique())
COL = {n: PAL[i % len(PAL)] for i, n in enumerate(LOCS)}
ACOL = {a: AREA_PAL[i % len(AREA_PAL)] for i, a in enumerate(AREAS)}
SLOTS = sorted(S["t"].unique())
SLOT_LABELS = [pd.Timestamp(x).strftime("%Y-%m-%d %H:%M") for x in SLOTS]


# ───────────────────────── sidebar (filter) ─────────────────────────
st.sidebar.header("Filter")
sel_area = st.sidebar.multiselect("Area", AREAS, default=AREAS)
sel_loc = st.sidebar.multiselect("Ruas", LOCS, default=LOCS)
t_from, t_to = st.sidebar.select_slider("Rentang waktu (WIB)", options=SLOT_LABELS,
                                        value=(SLOT_LABELS[0], SLOT_LABELS[-1]))
st.sidebar.caption("Ruas terpilih = irisan filter Area dan Ruas.")

area_of = dim.set_index("location_name")["area"].to_dict()
chosen = [n for n in sel_loc if area_of.get(n) in sel_area]
t0, t1 = pd.Timestamp(t_from), pd.Timestamp(t_to)
g = G[G["ruas"].isin(chosen) & (G["t"] >= t0) & (G["t"] <= t1)].copy()
s = S[S["ruas"].isin(chosen) & (S["t"] >= t0) & (S["t"] <= t1)].copy()


# ───────────────────────── header ─────────────────────────
st.title("🚦 UrbanPulse — Traffic & Weather Analytics")
n_hours = G["t"].nunique()
weekend_only = bool(G["is_weekend"].astype(bool).all())
rain_traffic = int((G["precipitation"] > 0).sum())
st.caption(f"Surabaya · {len(LOCS)} ruas · traffic {S['t'].min():%d %b %Y %H:%M} – {S['t'].max():%d %b %Y %H:%M} WIB "
           f"· cuaca {WC['t'].min():%d %b} – {WC['t'].max():%d %b %Y}")
st.warning(f"**Batasan data:** traffic hanya mencakup ±{n_hours} jam"
           f"{', seluruhnya akhir pekan' if weekend_only else ''}, dan "
           f"{'tidak ada hujan pada periode traffic' if rain_traffic == 0 else str(rain_traffic) + ' baris hujan'}. "
           "Pola jam bersifat deskriptif (tiap jam hanya muncul sekali); perbandingan hari kerja vs akhir pekan "
           "dan cuaca vs kecepatan belum dapat dianalisis.", icon="⚠️")


# ───────────────────────── statistik turunan ─────────────────────────
def loc_stats(g):
    if g.empty:
        return pd.DataFrame()
    rows = []
    for name, d in g.groupby("ruas"):
        ms, sd = d["speed"].mean(), d["speed"].std()
        rows.append({"ruas": name, "area": area_of[name], "n": len(d), "mean": d["cong"].mean(), "std": d["cong"].std(),
                     "median": d["cong"].median(), "p90": d["cong"].quantile(.9), "max": d["cong_max"].max(),
                     "zero": (d["cong"] == 0).mean(), "speed_mean": ms, "speed_std": sd,
                     "cv": sd / ms if ms and not np.isnan(sd) else np.nan})
    return pd.DataFrame(rows)


def hour_stats(g):
    if g.empty:
        return pd.DataFrame()
    return (g.groupby("hour").agg(mean=("cong", "mean"), share=("cong", lambda x: (x > 0).mean()), n=("cong", "size"))
              .reset_index().sort_values("hour"))


LS, HS = loc_stats(g), hour_stats(g)


# ───────────────────────── KPI & sorotan ─────────────────────────
def kpis():
    top = LS.sort_values("mean", ascending=False).iloc[0] if len(LS) else None
    peak = HS.sort_values("mean", ascending=False).iloc[0] if len(HS) else None
    stab = LS.dropna(subset=["cv"]).sort_values("cv") if len(LS) else LS
    r1, r2 = st.columns(3), st.columns(3)
    metric(r1[0], "Ruas congestion tertinggi", top["ruas"] if top is not None else "–",
           f"rata-rata {top['mean']:.3f}" if top is not None else "")
    metric(r1[1], "Jam puncak congestion", f"{int(peak['hour']):02d}:00" if peak is not None else "–",
           f"rata-rata {peak['mean']:.3f}" if peak is not None else "")
    metric(r1[2], "Rata-rata congestion", fmt(g["cong"].mean()) if len(g) else "–", f"{len(g)} ruas-jam")
    metric(r2[0], "Rata-rata kecepatan", f"{g['speed'].mean():.1f} km/jam" if len(g) else "–")
    metric(r2[1], "Kecepatan paling stabil", stab.iloc[0]["ruas"] if len(stab) else "–",
           f"CV {stab.iloc[0]['cv']:.3f}" if len(stab) else "")
    metric(r2[2], "Lonjakan congestion", str(int(s["spike"].sum())), f"dari {len(s)} slot (30 menit)")


def highlights():
    items = []
    if len(LS):
        a = LS.sort_values("mean", ascending=False)
        items.append(f"Rata-rata congestion tertinggi: **{a.iloc[0]['ruas']}** ({a.iloc[0]['mean']:.3f}); "
                     f"terendah: **{a.iloc[-1]['ruas']}** ({a.iloc[-1]['mean']:.3f}).")
        cv = LS.dropna(subset=["cv"]).sort_values("cv")
        if len(cv) > 1:
            items.append(f"Kecepatan paling stabil: **{cv.iloc[0]['ruas']}** (CV {cv.iloc[0]['cv']:.3f}); "
                         f"paling bervariasi: **{cv.iloc[-1]['ruas']}** (CV {cv.iloc[-1]['cv']:.3f}).")
    if len(HS):
        p = HS.sort_values("mean", ascending=False).iloc[0]
        items.append(f"Rata-rata congestion per jam tertinggi pada pukul **{int(p['hour']):02d}:00** "
                     f"({p['mean']:.3f}, {int(p['n'])} ruas-jam).")
    if len(g):
        items.append(f"{pct((g['cong'] == 0).mean())} ruas-jam memiliki congestion ratio 0.")
    if len(s):
        items.append(f"{int(s['spike'].sum())} slot ({pct(s['spike'].mean())}) tergolong lonjakan (Z-score dan IQR).")
    return items


def ordered(df, col, ycol="ruas"):
    return df.groupby(ycol)[col].mean().sort_values(ascending=False).index.tolist()


# ───────────────────────── tab ─────────────────────────
tabs = st.tabs(["Ringkasan", "Waktu & Tren", "Ruas & Area", "Hubungan & Anomali", "Cuaca", "Catatan Data"])
empty_msg = "Tidak ada data pada filter ini."

# ── Ringkasan
with tabs[0]:
    if g.empty:
        st.info(empty_msg)
    else:
        kpis()
        with st.container(border=True):
            st.markdown("**Sorotan data** (dihitung otomatis dari filter aktif)")
            st.markdown("\n".join(f"- {t}" for t in highlights()))
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Ranking congestion per ruas")
            st.caption("Q1 · rata-rata congestion ratio")
            area_legend()
            d = LS.sort_values("mean")
            fig = go.Figure(go.Bar(x=d["mean"], y=d["ruas"], orientation="h", marker_color=[ACOL[a] for a in d["area"]],
                                   text=d["mean"].map(lambda v: f"{v:.3f}"), textposition="outside", cliponaxis=False,
                                   customdata=np.c_[d["area"], d["max"], d["n"]],
                                   hovertemplate="<b>%{y}</b><br>Area %{customdata[0]}<br>rata-rata %{x:.4f}"
                                                 "<br>maks %{customdata[1]:.3f}<br>%{customdata[2]} jam<extra></extra>"))
            fig.update_layout(xaxis_title="rata-rata congestion ratio", height=420)
            show(fig, "q1")
        with c2:
            st.subheader("Congestion menurut jam")
            st.caption("Q2 · rata-rata dan porsi ruas-jam dengan congestion > 0")
            fig = make_subplots(specs=[[{"secondary_y": True}]])
            fig.add_bar(x=HS["hour"], y=HS["mean"], name="rata-rata congestion", marker_color="#e4572e")
            fig.add_scatter(x=HS["hour"], y=HS["share"], name="porsi ruas-jam > 0", mode="lines+markers",
                            line=dict(color="#2563eb"), secondary_y=True)
            fig.update_xaxes(title="jam (WIB)", dtick=1, range=[-0.6, 23.6])
            fig.update_yaxes(title_text="rata-rata congestion", secondary_y=False)
            fig.update_yaxes(title_text="porsi", tickformat=".0%", rangemode="tozero", secondary_y=True)
            fig.update_layout(height=420)
            show(fig, "q2")

# ── Waktu & Tren
with tabs[1]:
    if g.empty or s.empty:
        st.info(empty_msg)
    else:
        st.subheader("Heatmap ruas × jam")
        st.caption("Q2 · rata-rata congestion ratio (kosong = tidak ada data pada jam itu)")
        order = ordered(g, "cong")
        hm = g.pivot_table(index="ruas", columns="hour", values="cong", aggfunc="mean").reindex(index=order, columns=range(24))
        fig = go.Figure(go.Heatmap(z=hm.values, x=list(range(24)), y=hm.index, colorscale=SCALE, hoverongaps=False,
                                   colorbar=dict(title="congestion"),
                                   hovertemplate="%{y}<br>jam %{x}:00<br>%{z:.4f}<extra></extra>"))
        fig.update_layout(xaxis=dict(title="jam (WIB)", dtick=1), yaxis=dict(autorange="reversed"), height=420)
        show(fig, "hm_hour")

        st.subheader("Tren rata-rata semua ruas")
        st.caption("Q6 · resolusi 30 menit")
        city = s.groupby("t").agg(cong=("cong", "mean"), speed=("current_speed", "mean")).sort_index()
        city["cong_roll"] = city["cong"].rolling(3, center=True, min_periods=1).mean()
        city["speed_roll"] = city["speed"].rolling(3, center=True, min_periods=1).mean()
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_scatter(x=city.index, y=city["cong"], name="congestion", opacity=.35, line=dict(color="#e4572e"))
        fig.add_scatter(x=city.index, y=city["cong_roll"], name="congestion (rolling 3 slot)", line=dict(color="#e4572e", width=3))
        fig.add_scatter(x=city.index, y=city["speed_roll"], name="kecepatan (rolling 3 slot)", line=dict(color="#2563eb"),
                        secondary_y=True)
        fig.update_yaxes(title_text="congestion ratio", secondary_y=False)
        fig.update_yaxes(title_text="km/jam", secondary_y=True)
        fig.update_layout(hovermode="x unified", height=400)
        show(fig, "trend")

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Congestion per ruas dari waktu ke waktu")
            st.caption("Q6 · heatmap ruas × slot 30 menit")
            order = ordered(s, "cong")
            pv = s.pivot_table(index="ruas", columns="t", values="cong", aggfunc="mean").reindex(index=order)
            xs = [pd.Timestamp(x).strftime("%d %b %H:%M") for x in pv.columns]
            fig = go.Figure(go.Heatmap(z=pv.values, x=xs, y=pv.index, colorscale=SCALE, hoverongaps=False,
                                       colorbar=dict(title="congestion"),
                                       hovertemplate="%{y}<br>%{x}<br>%{z:.4f}<extra></extra>"))
            step = max(1, int(np.ceil(len(xs) / 5)))
            fig.update_layout(yaxis=dict(autorange="reversed"), height=420,
                              xaxis=dict(type="category", tickmode="array", tickvals=xs[::step], ticktext=xs[::step]))
            show(fig, "trend_hm")
        with c2:
            st.subheader("Kecepatan per ruas dari waktu ke waktu")
            st.caption("Q6 · km/jam")
            fig = px.line(s.sort_values("t"), x="t", y="current_speed", color="ruas", color_discrete_map=COL,
                          labels={"t": "waktu", "current_speed": "km/jam", "ruas": ""})
            fig.update_layout(height=420, hovermode="x unified")
            show(fig, "speed_lines")

# ── Ruas & Area
with tabs[2]:
    if g.empty:
        st.info(empty_msg)
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Stabilitas kecepatan")
            st.caption("Q5 · coefficient of variation (lebih kecil = lebih stabil)")
            area_legend()
            d = LS.dropna(subset=["cv"]).sort_values("cv", ascending=False)
            fig = go.Figure(go.Bar(x=d["cv"], y=d["ruas"], orientation="h", marker_color=[ACOL[a] for a in d["area"]],
                                   text=d["cv"].map(lambda v: f"{v:.3f}"), textposition="outside", cliponaxis=False,
                                   customdata=np.c_[d["speed_mean"], d["speed_std"]],
                                   hovertemplate="<b>%{y}</b><br>CV %{x:.4f}<br>rata-rata %{customdata[0]:.1f} km/jam"
                                                 "<br>std %{customdata[1]:.2f}<extra></extra>"))
            fig.update_layout(xaxis_title="CV kecepatan", height=420)
            show(fig, "q5")
        with c2:
            st.subheader("Perbandingan antararea")
            st.caption("Q7 · sebaran congestion per area (per jam)")
            fig = px.box(g, x="area", y="cong", color="area", color_discrete_map=ACOL, points="all",
                         labels={"cong": "congestion ratio", "area": ""})
            fig.update_layout(showlegend=False, height=360)
            show(fig, "q7")
            rows = []
            for a, d in g.groupby("area"):
                rows.append({"Area": a, "Ruas": d["ruas"].nunique(), "Rata-rata": round(d["cong"].mean(), 4),
                             "Median": round(d["cong"].median(), 4), "Maks": round(d["cong_max"].max(), 3),
                             "Porsi > 0": pct((d["cong"] > 0).mean()), "Speed (km/jam)": round(d["speed"].mean(), 1)})
            show_df(pd.DataFrame(rows).sort_values("Rata-rata", ascending=False), key="t_q7")

        st.subheader("Variasi congestion per ruas")
        st.caption("Q9 · diurutkan menurut standar deviasi")
        order = LS.sort_values("std", ascending=False)["ruas"].tolist()
        fig = px.box(g, x="ruas", y="cong", color="ruas", color_discrete_map=COL, points="all",
                     category_orders={"ruas": order}, labels={"cong": "congestion ratio", "ruas": ""})
        fig.update_layout(showlegend=False, height=420)
        show(fig, "q9")
        tbl = LS.sort_values("std", ascending=False)[["ruas", "n", "mean", "std", "median", "p90", "max", "zero"]].copy()
        tbl["zero"] = tbl["zero"].map(pct)
        tbl.columns = ["Ruas", "n", "Rata-rata", "Std", "Median", "P90", "Maks", "Porsi 0"]
        show_df(tbl.round(4), key="t_q9")

# ── Hubungan & Anomali
with tabs[3]:
    if s.empty:
        st.info(empty_msg)
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Kecepatan vs waktu tempuh")
            st.caption("Q8 · slot 30 menit; korelasi gabungan dan per ruas")
            fig = px.scatter(s, x="current_speed", y="current_travel_time", color="ruas", color_discrete_map=COL,
                             labels={"current_speed": "current_speed (km/jam)", "current_travel_time": "current_travel_time (detik)",
                                     "ruas": ""}, opacity=.8)
            fig.update_layout(height=420)
            show(fig, "q8")
            pooled = s[["current_speed", "current_travel_time"]].corr().iloc[0, 1]
            st.caption(f"Korelasi Pearson gabungan: **{fmt(pooled)}**. Waktu tempuh bergantung pada panjang ruas, jadi korelasi "
                       "di dalam tiap ruas lebih informatif (\"–\" = tidak ada variasi).")
            rows = []
            for name, d in s.groupby("ruas"):
                ok = d["current_speed"].nunique() > 1 and d["current_travel_time"].nunique() > 1
                rows.append({"Ruas": name, "n": len(d),
                             "Pearson (dalam ruas)": round(d["current_speed"].corr(d["current_travel_time"]), 3) if ok else None})
            show_df(pd.DataFrame(rows), key="t_q8")
        with c2:
            st.subheader("Lonjakan congestion")
            st.caption("Q10 · Z-score dan IQR per ruas (tanda X = lolos keduanya)")
            fig = px.scatter(s.sort_values("t"), x="t", y="cong", color="ruas", color_discrete_map=COL, opacity=.45,
                             labels={"t": "waktu (WIB)", "cong": "congestion ratio", "ruas": ""})
            sp = s[s["spike"]]
            fig.add_trace(go.Scatter(x=sp["t"], y=sp["cong"], mode="markers", name="lonjakan (Z & IQR)", text=sp["ruas"],
                                     marker=dict(symbol="x", size=12, color="black", line=dict(width=2, color="black")),
                                     hovertemplate="<b>%{text}</b><br>%{x}<br>%{y:.3f}<extra>lonjakan</extra>"))
            fig.update_layout(height=420)
            show(fig, "q10")
        st.caption(f"Lonjakan: **{int(s['spike'].sum())}** slot · Anomali kecepatan (|Z| > 3, global): "
                   f"**{int(s['speed_anom'].sum())}** slot. Deteksi dihitung pada seluruh data; filter hanya mengubah yang ditampilkan.")
        top = s[s["spike"]].sort_values("cong", ascending=False).head(15)
        if top.empty:
            st.caption("Tidak ada lonjakan pada filter ini.")
        else:
            out = top[["t", "ruas", "area", "current_speed", "free_flow_speed", "cong", "cong_z"]].copy()
            out["t"] = out["t"].dt.strftime("%Y-%m-%d %H:%M")
            out.columns = ["Waktu", "Ruas", "Area", "Speed", "Free-flow", "Congestion", "Z (ruas)"]
            show_df(out.round(3), key="t_q10")

# ── Cuaca
with tabs[4]:
    st.caption("Cuaca ditampilkan untuk seluruh periode dan tidak terpengaruh filter ruas. Area biru = rentang waktu traffic yang "
               "dipilih; area abu-abu = jam bermode `live`.")
    rain_h = WC[WC["rain"] > 0]
    days = WC.assign(day=WC["t"].dt.strftime("%Y-%m-%d")).groupby("day")["rain"].apply(lambda x: int((x > 0).sum()))
    c = st.columns(5)
    metric(c[0], "Rata-rata suhu", f"{WC['temp'].mean():.1f} °C")
    metric(c[1], "Jam hujan", str(len(rain_h)), f"dari {len(WC)} jam ({pct(len(rain_h) / len(WC))})")
    metric(c[2], "Hari dengan hujan", str(int((days > 0).sum())), f"dari {len(days)} hari")
    metric(c[3], "Hujan maks", f"{WC['rain'].max():.1f} mm/jam")
    metric(c[4], "Jam mode live", str(int(WC["live"].sum())))

    st.subheader("Suhu dan curah hujan per jam")
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_scatter(x=WC["t"], y=WC["temp"], name="suhu (°C)", line=dict(color="#e4572e"))
    fig.add_bar(x=WC["t"], y=WC["rain"], name="hujan maks (mm/jam)", marker_color="#2563eb", secondary_y=True)
    live = WC[WC["live"]]
    if len(live):
        fig.add_vrect(x0=live["t"].min(), x1=live["t"].max(), fillcolor="gray", opacity=.18, line_width=0)
    fig.add_vrect(x0=t0, x1=t1, fillcolor="#2563eb", opacity=.15, line_width=0)
    fig.update_yaxes(title_text="°C", secondary_y=False)
    fig.update_yaxes(title_text="mm/jam", secondary_y=True)
    fig.update_layout(hovermode="x unified", height=400)
    show(fig, "w1")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Jam hujan per hari")
        fig = go.Figure(go.Bar(x=days.index, y=days.values, marker_color="#2563eb"))
        fig.update_layout(yaxis_title="jam hujan", xaxis=dict(type="category", tickangle=-45), height=360)
        show(fig, "w2")
    with c2:
        st.subheader("Suhu rata-rata menurut jam")
        hh = WC.groupby(WC["t"].dt.hour)["temp"].mean()
        fig = go.Figure(go.Scatter(x=hh.index, y=hh.values, mode="lines+markers", line=dict(color="#e4572e")))
        fig.update_layout(xaxis=dict(title="jam (WIB)", dtick=2), yaxis_title="°C", height=360)
        show(fig, "w3")

# ── Catatan Data
with tabs[5]:
    st.subheader("Cakupan dan batasan data")
    st.markdown(
        f"- Traffic gold: {len(G)} baris ruas-jam ({G['t'].nunique()} jam unik × {G['ruas'].nunique()} ruas); silver: {len(S)} slot 30 menit.\n"
        f"- Periode traffic: {S['t'].min():%Y-%m-%d %H:%M} sampai {S['t'].max():%Y-%m-%d %H:%M} WIB; "
        f"hari kerja vs akhir pekan: {'semua akhir pekan' if weekend_only else 'campuran'}.\n"
        "- Observasi awal tiap ruas berjarak 1 jam, setelah itu 30 menit; slot yang hilang tampak sebagai sel kosong pada heatmap.\n"
        f"- Cuaca: {WC['t'].nunique()} jam × {META['n_weather_loc']} ruas; suhu antarruas pada jam yang sama hampir identik.\n"
        f"- Jam cuaca bermode live: {int(WC['live'].sum())}; selebihnya backfill."
    )
    st.subheader("Definisi")
    st.markdown(
        "- **Congestion ratio** = 1 − kecepatan saat ini / kecepatan arus bebas (minimum 0).\n"
        "- **Porsi macet** pada grafik jam = porsi ruas-jam dengan congestion ratio > 0 (belum ambang \"macet\" yang divalidasi).\n"
        "- **CV** = standar deviasi / rata-rata kecepatan per ruas (data gold per jam).\n"
        "- **Lonjakan (Q10)** = slot 30 menit dengan Z-score > 3 *dan* di atas fence IQR (Q3 + 1,5·IQR), dihitung per ruas pada seluruh data.\n"
        "- **Anomali kecepatan** = |Z-score global| > 3.\n"
        "- **Sumber**: layer Gold (`weather_traffic_hourly`, `dim_location`) dan Silver (`traffic_30min`, `weather_hourly`) proyek UrbanPulse. "
        "Sumber API cuaca belum dikonfirmasi (diduga reanalysis ERA5)."
    )
