"""
Веб-додаток «Календар активності горіння» (MODIS + VIIRS).
Запуск:  streamlit run app.py

Структура:
  firecal/load.py       — читання CSV з FIRMS, єдина схема
  firecal/harmonize.py  — гармонізація MODIS/VIIRS (сітка, об'єднання, калібрування)
  firecal/analytics.py  — календар, норма, аномалії, критичні періоди
  firecal/regions.py    — готові зони інтересу
"""
from __future__ import annotations

import io

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from firecal import analytics, harmonize, load
from firecal.regions import REGIONS

st.set_page_config(page_title="Календар горіння", page_icon="🔥", layout="wide")

MONTHS = ["Січ", "Лют", "Бер", "Кві", "Тра", "Чер", "Лип", "Сер", "Вер", "Жов", "Лис", "Гру"]


# ---------- дані (кешуються, щоб не перечитувати при кожному кліку) ----------
@st.cache_data(show_spinner="Читаю дані FIRMS…")
def get_points(uploaded: tuple[tuple[str, bytes], ...] = ()) -> pd.DataFrame:
    if uploaded:
        parts = [load.normalize(pd.read_csv(io.BytesIO(b))) for _, b in uploaded]
        return pd.concat(parts, ignore_index=True)
    return load.load_cached()


@st.cache_data(show_spinner="Гармонізую MODIS і VIIRS…")
def run_pipeline(points: pd.DataFrame, bbox, start, end, min_conf, cell_deg):
    viirs = points.loc[points["sensor"] == "VIIRS", "date"]
    viirs_start = viirs.min() if not viirs.empty else None
    p = harmonize.filter_points(points, bbox=bbox, start=start, end=end, min_conf=min_conf)
    cells = harmonize.to_fire_cell_days(p, cell_deg=cell_deg)
    daily = harmonize.daily_counts(cells, start=start, end=end)
    activity, k = harmonize.harmonized_series(daily, viirs_start)
    return p, cells, daily, activity, k, viirs_start


# ---------- бічна панель: зона інтересу і параметри ----------
st.sidebar.title("🔥 Календар горіння")
with st.sidebar.expander("📂 Свої дані FIRMS (CSV)", expanded=False):
    files = st.file_uploader("MODIS і/або VIIRS CSV", type="csv", accept_multiple_files=True)
uploaded = tuple((f.name, f.getvalue()) for f in files) if files else ()
points = get_points(uploaded)

if points.empty:
    st.warning("Немає даних. Покладіть CSV з FIRMS у `data/raw/` або запустіть "
               "`python scripts/make_sample_data.py` для тестових даних.")
    st.stop()

region = st.sidebar.selectbox("Зона інтересу", list(REGIONS) + ["Свій прямокутник"])
if region == "Свій прямокутник":
    c1, c2 = st.sidebar.columns(2)
    lon0 = c1.number_input("Довгота від", value=30.0)
    lon1 = c2.number_input("Довгота до", value=34.0)
    lat0 = c1.number_input("Широта від", value=46.0)
    lat1 = c2.number_input("Широта до", value=48.0)
    bbox = (lon0, lat0, lon1, lat1)
else:
    bbox = REGIONS[region]

y_min, y_max = int(points["date"].dt.year.min()), int(points["date"].dt.year.max())
years = st.sidebar.slider("Роки", y_min, y_max, (y_min, y_max))
min_conf = st.sidebar.select_slider("Мін. достовірність", ["low", "nominal", "high"], value="nominal")
cell_deg = st.sidebar.select_slider("Розмір клітинки сітки, °", [0.05, 0.1, 0.25, 0.5], value=0.1)

if any(n.startswith("SAMPLE") for n in [f.name for f in load.RAW_DIR.glob("*.csv")]) and not uploaded:
    st.sidebar.error("⚠️ Зараз показано СИНТЕТИЧНІ тестові дані. Замініть їх справжніми з FIRMS.")

start, end = pd.Timestamp(years[0], 1, 1), pd.Timestamp(years[1], 12, 31)
pts, cells, daily, activity, k, viirs_start = run_pipeline(points, bbox, start, end, min_conf, cell_deg)

# ---------- шапка з ключовими цифрами ----------
st.title(f"Календар активності горіння — {region}")
clim = analytics.climatology(activity)
anom = analytics.anomalies(activity)
crit = analytics.critical_periods(activity)
peak_week = int(clim["mean"].idxmax()) if not clim.empty else 0
m1, m2, m3, m4 = st.columns(4)
m1.metric("Пожежо-клітинко-днів (гарм.)", f"{activity.sum():,.0f}")
m2.metric("Пік сезону", f"тиждень {peak_week}" if peak_week else "—",
          pd.Timestamp.fromisocalendar(2021, peak_week, 1).strftime("~%d.%m") if peak_week else None)
m3.metric("Аномальних тижнів", len(anom[anom["kind"] == "вище норми"]))
m4.metric("Точок MODIS / VIIRS", f"{(pts.sensor == 'MODIS').sum():,} / {(pts.sensor == 'VIIRS').sum():,}")

tab_cal, tab_crit, tab_anom, tab_map, tab_cmp, tab_about = st.tabs(
    ["📅 Календар", "🔥 Критичні періоди", "⚠️ Аномалії", "🗺️ Карта", "🛰️ MODIS vs VIIRS", "ℹ️ Методика"])

# ---------- 1. Календар ----------
with tab_cal:
    c1, c2 = st.columns([3, 1])
    series_name = c2.radio("Ряд", ["Гармонізований", "Лише MODIS", "Лише VIIRS"],
                           help="Перемкніть на 'Лише MODIS'/'Лише VIIRS', щоб побачити, чому без гармонізації роки непорівнянні")
    freq = c2.radio("Крок", ["Тиждень", "Місяць"])
    series = {"Гармонізований": activity, "Лише MODIS": daily["modis"], "Лише VIIRS": daily["viirs"]}[series_name]
    mat = analytics.calendar_matrix(series, "month" if freq == "Місяць" else "week")
    fig = px.imshow(
        mat, aspect="auto", color_continuous_scale="YlOrRd", origin="lower",
        labels=dict(x="Місяць" if freq == "Місяць" else "Тиждень року", y="Рік", color="Активність"),
        x=MONTHS if freq == "Місяць" else list(mat.columns), y=[str(y) for y in mat.index],
    )
    fig.update_layout(height=max(350, 22 * len(mat) + 120), margin=dict(l=0, r=0, t=30, b=0))
    c1.plotly_chart(fig, width="stretch")
    c2.caption("Кожна клітинка = скільки ділянок сітки горіло за цей період. "
               "Темніше = більше горіння.")

# ---------- 2. Критичні періоди ----------
with tab_crit:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=clim.index, y=clim["p90"], line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=clim.index, y=clim["median"], fill="tonexty", name="Медіана (діапазон до 90-го перцентиля)",
                             line=dict(color="#e4572e")))
    fig.add_trace(go.Scatter(x=clim.index, y=clim["mean"], name="Середнє", line=dict(color="#333", dash="dot")))
    fig.update_layout(xaxis_title="Тиждень року", yaxis_title="Активність за тиждень", height=380,
                      margin=dict(l=0, r=0, t=30, b=0), legend=dict(orientation="h"))
    st.plotly_chart(fig, width="stretch")
    st.subheader("Критичні періоди (найактивніші 25% тижнів року)")
    st.dataframe(crit, hide_index=True, width="stretch")

# ---------- 3. Аномалії ----------
with tab_anom:
    st.caption("Тиждень вважається аномальним, якщо відхилення від багаторічної норми цього тижня "
               "більше ніж 2 робастні стандартні відхилення (медіана/MAD).")
    if anom.empty:
        st.info("Аномалій не знайдено.")
    else:
        a = anom.copy()
        a["дата"] = [pd.Timestamp.fromisocalendar(int(y), int(w), 1).strftime("%d.%m.%Y") for y, w in zip(a.year, a.week)]
        st.dataframe(a.rename(columns={"year": "рік", "week": "тиждень", "value": "активність",
                                       "norm": "норма", "kind": "тип"})[["рік", "тиждень", "дата", "активність", "норма", "z", "тип"]]
                     .round(1), hide_index=True, width="stretch")
    yearly = activity.groupby(activity.index.year).sum()
    fig = px.bar(x=yearly.index, y=yearly.values, labels=dict(x="Рік", y="Активність за рік"),
                 color=yearly.values, color_continuous_scale="YlOrRd")
    fig.add_hline(y=yearly.median(), line_dash="dot", annotation_text="медіана")
    fig.update_layout(height=320, margin=dict(l=0, r=0, t=30, b=0), coloraxis_showscale=False)
    st.plotly_chart(fig, width="stretch")

# ---------- 4. Карта ----------
with tab_map:
    if cells.empty:
        st.info("Немає пожеж у вибраній зоні/періоді.")
    else:
        months = st.multiselect("Місяці", list(range(1, 13)), default=list(range(1, 13)),
                                format_func=lambda m: MONTHS[m - 1])
        cm = cells[cells["date"].dt.month.isin(months)]
        agg = cm.groupby(["lat", "lon"], as_index=False).size().rename(columns={"size": "днів горіння"})
        fig = px.density_map(agg, lat="lat", lon="lon", z="днів горіння", radius=12,
                             center=dict(lat=(bbox[1] + bbox[3]) / 2, lon=(bbox[0] + bbox[2]) / 2),
                             zoom=5 if region.startswith("Україна") else 6, map_style="carto-positron",
                             color_continuous_scale="YlOrRd")
        fig.update_layout(height=600, margin=dict(l=0, r=0, t=0, b=0))
        st.plotly_chart(fig, width="stretch")

# ---------- 5. Порівняння датчиків ----------
with tab_cmp:
    st.markdown("**Чому не можна просто скласти точки?** VIIRS дає у кілька разів більше точок на ту саму пожежу. "
                "Нижче — сирі точки проти гармонізованих пожежо-клітинко-днів.")
    raw = pts.groupby([pts["date"].dt.year, "sensor"]).size().unstack(fill_value=0)
    raw.index.name = "рік"
    yr = daily.groupby(daily.index.year)[["modis", "viirs", "union"]].sum()
    yr["гармонізований"] = activity.groupby(activity.index.year).sum()
    c1, c2 = st.columns(2)
    c1.plotly_chart(px.line(raw, markers=True, title="Сирі точки за рік",
                            labels=dict(value="точок", variable="датчик")), width="stretch")
    c2.plotly_chart(px.line(yr, markers=True, title="Пожежо-клітинко-дні за рік",
                            labels=dict(value="клітинко-днів", variable="ряд")), width="stretch")
    st.markdown(f"Початок VIIRS у даних: **{viirs_start:%d.%m.%Y}**" if viirs_start is not None else "VIIRS у даних немає.")
    st.markdown("**Коефіцієнти калібрування k** (union / MODIS у період перекриття, по місяцях):")
    st.dataframe(pd.DataFrame([k.round(2).values], columns=MONTHS), hide_index=True)

# ---------- 6. Методика ----------
with tab_about:
    st.markdown("""
### Як ми гармонізуємо MODIS і VIIRS
1. **Єдина схема.** Обидва формати FIRMS зводимо до спільних полів; достовірність MODIS (0–100) і VIIRS (l/n/h)
   — до трьох класів *low / nominal / high* (MODIS: <30 low, ≥80 high). Лишаємо лише рослинні пожежі (`type = 0`).
2. **Спільна сітка.** Точки «прилипають» до клітинок (за замовч. 0.1° ≈ 8×11 км). Одиниця — **пожежо-клітинко-день**:
   клітинка, де за добу був хоч один хот-спот. Це прибирає різницю в розмірі пікселя (1 км vs 375 м).
3. **Об'єднання.** Клітинко-день активний, якщо його побачив MODIS **або** VIIRS.
4. **Калібрування.** У роки, де працюють обидва датчики, рахуємо k = union / MODIS для кожного місяця.
   Для років лише з MODIS (до 2012) ряд = MODIS × k. Так виходить безперервний ряд з 2000 р.
5. **Календар.** Рік × тиждень; **норма** = медіана по роках; **аномалія** = робастний z ≥ 2;
   **критичні періоди** = 25% тижнів із найбільшою середньою активністю.

Джерело даних: NASA FIRMS (MODIS C6.1, VIIRS S-NPP 375 м).
""")
