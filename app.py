"""
Веб-додаток «Календар активності горіння» (MODIS + VIIRS).
Запуск:  streamlit run app.py

ЯК ПРАЦЮЄ STREAMLIT (важливо зрозуміти один раз):
  * Цей файл — звичайний Python-скрипт, який виконується ЗВЕРХУ ВНИЗ.
  * Кожен виклик st.щось(...) малює елемент на сторінці (заголовок, графік, кнопку).
  * Коли користувач щось змінює (слайдер, список), Streamlit ПЕРЕЗАПУСКАЄ весь
    скрипт з нуля, і віджет повертає нове значення. Тобто окремого "бекенду" й
    "фронтенду" немає: усе в одному файлі, а HTML/JS Streamlit генерує сам.
  * Щоб важкі обчислення не повторювались при кожному кліку, функції позначаємо
    @st.cache_data — результат запам'ятовується для тих самих аргументів.

Структура проєкту:
  firecal/load.py       — читання CSV з FIRMS, єдина схема
  firecal/harmonize.py  — гармонізація MODIS/VIIRS (сітка, об'єднання, калібрування)
  firecal/analytics.py  — календар, норма, аномалії, критичні періоди
  firecal/regions.py    — готові зони інтересу
"""
from __future__ import annotations  # дозволяє писати типи на кшталт tuple[int, int] у старших Python

import io  # io.BytesIO — щоб читати завантажений файл з пам'яті, як звичайний файл

import pandas as pd                 # таблиці (DataFrame) — основний інструмент роботи з даними
import plotly.express as px         # швидкі інтерактивні графіки "одним рядком"
import plotly.graph_objects as go   # "ручні" графіки, коли треба кілька ліній на одному
import streamlit as st              # веб-інтерфейс

# Наш власний код із папки firecal/ (див. опис структури вище)
from firecal import analytics, harmonize, load
from firecal.regions import REGIONS

# Налаштування сторінки: назва вкладки браузера, іконка, широкий макет. Має бути ПЕРШИМ st-викликом.
st.set_page_config(page_title="Календар горіння", page_icon="🔥", layout="wide")

# Підписи місяців для графіків (індекс 0 = січень)
MONTHS = ["Січ", "Лют", "Бер", "Кві", "Тра", "Чер", "Лип", "Сер", "Вер", "Жов", "Лис", "Гру"]


# =====================================================================
# ДАНІ. Функції з @st.cache_data виконуються лише раз для однакових аргументів.
# =====================================================================
@st.cache_data(show_spinner="Читаю дані FIRMS…")
def get_points(uploaded: tuple[tuple[str, bytes], ...] = ()) -> pd.DataFrame:
    """Повертає всі точки-хот-спіти в єдиній схемі.

    uploaded — файли, які користувач завантажив через сайт: кортеж пар (ім'я, байти).
    Якщо нічого не завантажено — читаємо CSV з папки data/raw/.
    """
    if uploaded:
        # для кожного файлу: байти -> таблиця -> єдина схема; потім склеюємо всі в одну
        parts = [load.normalize(pd.read_csv(io.BytesIO(b))) for _, b in uploaded]
        return pd.concat(parts, ignore_index=True)
    return load.load_cached()


@st.cache_data(show_spinner="Гармонізую MODIS і VIIRS…")
def run_pipeline(points: pd.DataFrame, bbox, start, end, min_conf, cell_deg):
    """Увесь конвеєр обробки: точки -> відбір -> клітинки -> денні суми -> гармонізований ряд.

    Повертає:
      p           — точки після фільтрів (зона, роки, достовірність)
      cells       — пожежо-клітинко-дні (одна строка = одна клітинка сітки, що горіла в один день)
      daily       — таблиця по днях: скільки клітинок бачив MODIS, VIIRS, обидва, разом (union)
      activity    — ГОЛОВНИЙ результат: єдиний ряд активності горіння по днях
      k           — коефіцієнти калібрування для кожного місяця
      viirs_start — дата, з якої в даних з'являється VIIRS
    """
    # Дату початку VIIRS беремо з УСІХ даних (не лише вибраної зони),
    # бо це властивість супутника, а не регіону.
    viirs = points.loc[points["sensor"] == "VIIRS", "date"]
    viirs_start = viirs.min() if not viirs.empty else None

    p = harmonize.filter_points(points, bbox=bbox, start=start, end=end, min_conf=min_conf)
    cells = harmonize.to_fire_cell_days(p, cell_deg=cell_deg)
    daily = harmonize.daily_counts(cells, start=start, end=end)
    activity, k = harmonize.harmonized_series(daily, viirs_start)
    return p, cells, daily, activity, k, viirs_start


# =====================================================================
# БІЧНА ПАНЕЛЬ: користувач вибирає зону інтересу і параметри.
# Кожен віджет просто ПОВЕРТАЄ вибране значення у змінну.
# =====================================================================
st.sidebar.title("🔥 Календар горіння")

# Розгортний блок для завантаження власних CSV (необов'язково)
with st.sidebar.expander("📂 Свої дані FIRMS (CSV)", expanded=False):
    files = st.file_uploader("MODIS і/або VIIRS CSV", type="csv", accept_multiple_files=True)
# Перетворюємо файли в кортеж (ім'я, байти) — такий формат кеш уміє порівнювати
uploaded = tuple((f.name, f.getvalue()) for f in files) if files else ()
points = get_points(uploaded)

# Якщо даних немає взагалі — показуємо підказку і зупиняємо скрипт (st.stop)
if points.empty:
    st.warning("Немає даних. Покладіть CSV з FIRMS у `data/raw/` або запустіть "
               "`python scripts/make_sample_data.py` для тестових даних.")
    st.stop()

# Вибір зони: або готова з REGIONS, або свій прямокутник з координат
region = st.sidebar.selectbox("Зона інтересу", list(REGIONS) + ["Свій прямокутник"])
if region == "Свій прямокутник":
    c1, c2 = st.sidebar.columns(2)  # дві колонки поруч: "від" і "до"
    lon0 = c1.number_input("Довгота від", value=30.0)
    lon1 = c2.number_input("Довгота до", value=34.0)
    lat0 = c1.number_input("Широта від", value=46.0)
    lat1 = c2.number_input("Широта до", value=48.0)
    bbox = (lon0, lat0, lon1, lat1)  # bbox = "bounding box", прямокутник (захід, південь, схід, північ)
else:
    bbox = REGIONS[region]

# Слайдер років: межі беремо з самих даних; (y_min, y_max) як значення = діапазон із двома повзунками
y_min, y_max = int(points["date"].dt.year.min()), int(points["date"].dt.year.max())
years = st.sidebar.slider("Роки", y_min, y_max, (y_min, y_max))
# Мінімальна достовірність: low пропускає все, high — лише найнадійніші точки
min_conf = st.sidebar.select_slider("Мін. достовірність", ["low", "nominal", "high"], value="nominal")
# Розмір клітинки сітки в градусах (0.1° ≈ 8×11 км в Україні). Менше = детальніше, але шумніше.
cell_deg = st.sidebar.select_slider("Розмір клітинки сітки, °", [0.05, 0.1, 0.25, 0.5], value=0.1)

# Попередження, якщо в data/raw/ лежать синтетичні файли (їхні назви починаються з SAMPLE)
if any(n.startswith("SAMPLE") for n in [f.name for f in load.RAW_DIR.glob("*.csv")]) and not uploaded:
    st.sidebar.error("⚠️ Зараз показано СИНТЕТИЧНІ тестові дані. Замініть їх справжніми з FIRMS.")

# Перетворюємо вибрані роки в дати: з 1 січня першого року до 31 грудня останнього
start, end = pd.Timestamp(years[0], 1, 1), pd.Timestamp(years[1], 12, 31)
# Запускаємо обробку (з кешу, якщо параметри не змінились)
pts, cells, daily, activity, k, viirs_start = run_pipeline(points, bbox, start, end, min_conf, cell_deg)

# =====================================================================
# ШАПКА: заголовок і 4 ключові цифри (st.metric = велике число з підписом)
# =====================================================================
st.title(f"Календар активності горіння — {region}")
clim = analytics.climatology(activity)        # "норма" для кожного тижня року
anom = analytics.anomalies(activity)          # тижні, що сильно відхиляються від норми
crit = analytics.critical_periods(activity)   # найактивніші періоди року
# idxmax() повертає ІНДЕКС максимального значення, тобто номер тижня з найбільшою активністю
peak_week = int(clim["mean"].idxmax()) if not clim.empty else 0

m1, m2, m3, m4 = st.columns(4)  # 4 колонки в ряд
m1.metric("Пожежо-клітинко-днів (гарм.)", f"{activity.sum():,.0f}")  # :,.0f = з роздільником тисяч, без дробів
# Третій аргумент metric — маленький підпис під числом: приблизна дата початку пікового тижня.
# fromisocalendar(рік, тиждень, день) -> дата; 2021 тут просто "будь-який рік" для перетворення.
m2.metric("Пік сезону", f"тиждень {peak_week}" if peak_week else "—",
          pd.Timestamp.fromisocalendar(2021, peak_week, 1).strftime("~%d.%m") if peak_week else None)
m3.metric("Аномальних тижнів", len(anom[anom["kind"] == "вище норми"]))
# (pts.sensor == 'MODIS').sum() — рахуємо True, тобто кількість точок MODIS
m4.metric("Точок MODIS / VIIRS", f"{(pts.sensor == 'MODIS').sum():,} / {(pts.sensor == 'VIIRS').sum():,}")

# Вкладки. Усе, що всередині "with tab_x:", малюється на відповідній вкладці.
tab_cal, tab_crit, tab_anom, tab_map, tab_cmp, tab_about = st.tabs(
    ["📅 Календар", "🔥 Критичні періоди", "⚠️ Аномалії", "🗺️ Карта", "🛰️ MODIS vs VIIRS", "ℹ️ Методика"])

# =====================================================================
# 1. КАЛЕНДАР — теплова карта "рік × тиждень". Головний візуал проєкту.
# =====================================================================
with tab_cal:
    c1, c2 = st.columns([3, 1])  # ліва колонка втричі ширша за праву
    series_name = c2.radio("Ряд", ["Гармонізований", "Лише MODIS", "Лише VIIRS"],
                           help="Перемкніть на 'Лише MODIS'/'Лише VIIRS', щоб побачити, чому без гармонізації роки непорівнянні")
    freq = c2.radio("Крок", ["Тиждень", "Місяць"])
    # Словник "назва -> ряд даних", з якого беремо вибраний варіант
    series = {"Гармонізований": activity, "Лише MODIS": daily["modis"], "Лише VIIRS": daily["viirs"]}[series_name]
    # Таблиця: рядки = роки, стовпці = тижні (або місяці), значення = активність
    mat = analytics.calendar_matrix(series, "month" if freq == "Місяць" else "week")
    # px.imshow малює таблицю як картинку: кожна клітинка = колір за значенням
    fig = px.imshow(
        mat, aspect="auto", color_continuous_scale="YlOrRd", origin="lower",  # origin="lower" — старі роки знизу
        labels=dict(x="Місяць" if freq == "Місяць" else "Тиждень року", y="Рік", color="Активність"),
        x=MONTHS if freq == "Місяць" else list(mat.columns), y=[str(y) for y in mat.index],
    )
    # Висота залежить від кількості років, щоб рядки не були занадто тонкими
    fig.update_layout(height=max(350, 22 * len(mat) + 120), margin=dict(l=0, r=0, t=30, b=0))
    c1.plotly_chart(fig, width="stretch")
    c2.caption("Кожна клітинка = скільки ділянок сітки горіло за цей період. "
               "Темніше = більше горіння.")

# =====================================================================
# 2. КРИТИЧНІ ПЕРІОДИ — "типовий рік": як змінюється горіння по тижнях
# =====================================================================
with tab_crit:
    fig = go.Figure()
    # Трюк для заливки діапазону: спочатку невидима лінія 90-го перцентиля,
    # потім медіана з fill="tonexty" — заливається простір між ними.
    fig.add_trace(go.Scatter(x=clim.index, y=clim["p90"], line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=clim.index, y=clim["median"], fill="tonexty", name="Медіана (діапазон до 90-го перцентиля)",
                             line=dict(color="#e4572e")))
    fig.add_trace(go.Scatter(x=clim.index, y=clim["mean"], name="Середнє", line=dict(color="#333", dash="dot")))
    fig.update_layout(xaxis_title="Тиждень року", yaxis_title="Активність за тиждень", height=380,
                      margin=dict(l=0, r=0, t=30, b=0), legend=dict(orientation="h"))
    st.plotly_chart(fig, width="stretch")
    st.subheader("Критичні періоди (найактивніші 25% тижнів року)")
    st.dataframe(crit, hide_index=True, width="stretch")  # таблиця з сортуванням по кліку

# =====================================================================
# 3. АНОМАЛІЇ — конкретні тижні конкретних років, що вибиваються з норми
# =====================================================================
with tab_anom:
    st.caption("Тиждень вважається аномальним, якщо відхилення від багаторічної норми цього тижня "
               "більше ніж 2 робастні стандартні відхилення (медіана/MAD).")
    if anom.empty:
        st.info("Аномалій не знайдено.")
    else:
        a = anom.copy()  # копія, щоб не зіпсувати закешовані дані
        # Для кожної пари (рік, тиждень) рахуємо дату понеділка цього тижня — людям зрозуміліше за номер тижня
        a["дата"] = [pd.Timestamp.fromisocalendar(int(y), int(w), 1).strftime("%d.%m.%Y") for y, w in zip(a.year, a.week)]
        # rename — українські назви колонок; [[...]] — вибір і порядок колонок; round(1) — 1 знак після коми
        st.dataframe(a.rename(columns={"year": "рік", "week": "тиждень", "value": "активність",
                                       "norm": "норма", "kind": "тип"})[["рік", "тиждень", "дата", "активність", "норма", "z", "тип"]]
                     .round(1), hide_index=True, width="stretch")
    # Стовпчики "активність за кожен рік" + пунктир медіани: видно аномальні роки цілком
    yearly = activity.groupby(activity.index.year).sum()  # групуємо дні за роком і сумуємо
    fig = px.bar(x=yearly.index, y=yearly.values, labels=dict(x="Рік", y="Активність за рік"),
                 color=yearly.values, color_continuous_scale="YlOrRd")
    fig.add_hline(y=yearly.median(), line_dash="dot", annotation_text="медіана")
    fig.update_layout(height=320, margin=dict(l=0, r=0, t=30, b=0), coloraxis_showscale=False)
    st.plotly_chart(fig, width="stretch")

# =====================================================================
# 4. КАРТА — де горить найчастіше (теплова карта щільності)
# =====================================================================
with tab_map:
    if cells.empty:
        st.info("Немає пожеж у вибраній зоні/періоді.")
    else:
        # Вибір місяців: format_func показує "Січ" замість 1
        months = st.multiselect("Місяці", list(range(1, 13)), default=list(range(1, 13)),
                                format_func=lambda m: MONTHS[m - 1])
        cm = cells[cells["date"].dt.month.isin(months)]  # лишаємо лише вибрані місяці
        # Для кожної клітинки рахуємо, скільки днів вона горіла (.size() = кількість рядків у групі)
        agg = cm.groupby(["lat", "lon"], as_index=False).size().rename(columns={"size": "днів горіння"})
        # density_map — "розмита" теплова карта на підкладці OpenStreetMap/Carto (потрібен інтернет)
        fig = px.density_map(agg, lat="lat", lon="lon", z="днів горіння", radius=12,
                             center=dict(lat=(bbox[1] + bbox[3]) / 2, lon=(bbox[0] + bbox[2]) / 2),  # центр зони
                             zoom=5 if region.startswith("Україна") else 6, map_style="carto-positron",
                             color_continuous_scale="YlOrRd")
        fig.update_layout(height=600, margin=dict(l=0, r=0, t=0, b=0))
        st.plotly_chart(fig, width="stretch")

# =====================================================================
# 5. MODIS vs VIIRS — доказ для журі, НАВІЩО потрібна гармонізація
# =====================================================================
with tab_cmp:
    st.markdown("**Чому не можна просто скласти точки?** VIIRS дає у кілька разів більше точок на ту саму пожежу. "
                "Нижче — сирі точки проти гармонізованих пожежо-клітинко-днів.")
    # Сирі точки: групуємо за (рік, датчик), рахуємо кількість, unstack розгортає датчики в окремі колонки
    raw = pts.groupby([pts["date"].dt.year, "sensor"]).size().unstack(fill_value=0)
    raw.index.name = "рік"
    # Ті самі роки, але в клітинко-днях + наш гармонізований ряд
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

# =====================================================================
# 6. МЕТОДИКА — текст для журі (Markdown)
# =====================================================================
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
