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
from firecal import analytics, export, harmonize, load, mapgrid, regions

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
        # для кожного файлу: байти -> таблиця -> єдина схема; потім склеюємо всі в одну без дублікатів
        pts = load.combine([load.normalize(pd.read_csv(io.BytesIO(b))) for _, b in uploaded])
    else:
        pts = load.load_cached()
    # Один раз визначаємо область кожної точки (миттєво, див. regions.oblast_of) —
    # далі фільтр за областями просто дивиться в цю колонку
    if not pts.empty:
        pts["oblast"] = regions.oblast_of(pts["lat"], pts["lon"])
    return pts


@st.cache_data(show_spinner="Гармонізую MODIS і VIIRS…")
def run_pipeline(points: pd.DataFrame, bbox, start, end, min_conf, cell_deg, oblasts, exclude_war):
    """Увесь конвеєр обробки: точки -> відбір -> клітинки -> денні суми -> гармонізований ряд.

    Повертає:
      p           — точки після фільтрів (зона, роки, достовірність)
      cells       — пожежо-клітинко-дні (одна строка = одна клітинка сітки, що горіла в один день)
      daily       — таблиця по днях: скільки клітинок бачив MODIS, VIIRS, обидва, разом (union)
      activity    — ГОЛОВНИЙ результат: єдиний ряд активності горіння по днях
      k           — коефіцієнти калібрування для кожного місяця
      viirs_start — дата, з якої в даних з'являється VIIRS

    Сама логіка — у firecal/harmonize.py (там і пояснення, чому k рахується на всіх роках).
    Тут лише кеш: @st.cache_data не перераховує, якщо параметри ті самі.
    """
    return harmonize.run_pipeline(points, bbox, start, end, min_conf=min_conf, cell_deg=cell_deg,
                                  oblasts=oblasts, exclude_war=exclude_war)


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

# Вибір зони: група областей, окрема область (справжні межі, regions.ZONES) або свій прямокутник
region = st.sidebar.selectbox("Зона інтересу", list(regions.ZONES) + ["Свій прямокутник"],
                              help="Межі областей — справжні (з точністю ~1 км), а не прямокутники.")
oblasts = None  # None = без фільтра за областями
if region == "Свій прямокутник":
    c1, c2 = st.sidebar.columns(2)  # дві колонки поруч: "від" і "до"
    lon0 = c1.number_input("Довгота від", value=30.0)
    lon1 = c2.number_input("Довгота до", value=34.0)
    lat0 = c1.number_input("Широта від", value=46.0)
    lat1 = c2.number_input("Широта до", value=48.0)
    bbox = (lon0, lat0, lon1, lat1)  # bbox = "bounding box", прямокутник (захід, південь, схід, північ)
    map_box = bbox
else:
    oblasts = regions.ZONES[region]
    bbox = None                         # фільтруємо за областями, а не прямокутником
    map_box = regions.bbox_of(oblasts)  # прямокутник навколо областей — лише щоб відцентрувати карту

# Перемикач "без зони бойових дій": з 24.02.2022 у прифронтових областях багато хот-спотів
# від обстрілів, а не від с/г палів — вони спотворюють календар і аномалії
exclude_war = st.sidebar.checkbox(
    "Без прифронтових областей з 24.02.2022",
    help="Викидає точки з областей " + ", ".join(regions.FRONTLINE) + " після початку повномасштабної "
         "війни: там багато хот-спотів від обстрілів, а не від пожеж рослинності.")

# Слайдер років: межі беремо з самих даних; третій аргумент — діапазон за замовчуванням.
# default_years відкидає неповні роки на краях (напр. 2000, де MODIS лише з листопада).
y_min, y_max = int(points["date"].dt.year.min()), int(points["date"].dt.year.max())
years = st.sidebar.slider("Роки", y_min, y_max, analytics.default_years(points["date"]),
                          help="Неповні роки на краях даних (напр. 2000 — MODIS лише з листопада) "
                               "за замовчуванням вимкнені, бо виглядають як провал.")
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
# tuple(oblasts) — кеш Streamlit краще працює з кортежами, ніж зі списками
pts, cells, daily, activity, k, viirs_start = run_pipeline(
    points, bbox, start, end, min_conf, cell_deg, tuple(oblasts) if oblasts else None, exclude_war)

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
# week_start(рік, тиждень) -> дата першого дня тижня; 2021 тут просто "будь-який рік" для перетворення.
m2.metric("Пік сезону", f"тиждень {peak_week}" if peak_week else "—",
          analytics.week_start(2021, peak_week).strftime("~%d.%m") if peak_week else None)
m3.metric("Аномальних тижнів", len(anom[anom["kind"] == "вище норми"]))
# (pts.sensor == 'MODIS').sum() — рахуємо True, тобто кількість точок MODIS
m4.metric("Точок MODIS / VIIRS", f"{(pts.sensor == 'MODIS').sum():,} / {(pts.sensor == 'VIIRS').sum():,}")

# Вкладки. Усе, що всередині "with tab_x:", малюється на відповідній вкладці.
tab_cal, tab_warn, tab_crit, tab_anom, tab_map, tab_cmp, tab_about = st.tabs(
    ["📅 Календар", "🚨 Раннє попередження", "🔥 Критичні періоди", "⚠️ Аномалії", "🗺️ Карта",
     "🛰️ MODIS vs VIIRS", "ℹ️ Методика"])

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
    # Кнопка завантаження: віддаємо ту саму таблицю (рік × тиждень/місяць), що намальована як теплова карта.
    # rename_axis("рік") підписує стовпець з роками. У місячному режимі стовпці — числа 1..12,
    # тож міняємо їх на назви місяців (як на графіку). export.to_excel_csv — CSV, що відкривається в Excel.
    mat_out = mat.rename_axis("рік").round(1)
    if freq == "Місяць":
        mat_out.columns = MONTHS
    # Код ряду в назві файлу: скачавши гармонізований, MODIS і VIIRS, їх легко розрізнити
    series_code = {"Гармонізований": "harmonized", "Лише MODIS": "modis", "Лише VIIRS": "viirs"}[series_name]
    c2.download_button(
        "⬇️ Скачати CSV",
        data=export.to_excel_csv(mat_out, index=True),  # index=True: роки — це індекс, їх лишаємо
        file_name=f"calendar_{'month' if freq == 'Місяць' else 'week'}_{series_code}.csv",
        mime="text/csv",
        key="dl_calendar",  # унікальний ключ, щоб не конфліктувало з кнопкою на іншій вкладці
    )
    c2.caption("Кожна клітинка = скільки ділянок сітки горіло за цей період. "
               "Темніше = більше горіння.")

# =====================================================================
# РАННЄ ПОПЕРЕДЖЕННЯ — "наскільки зараз горить більше, ніж зазвичай у цю пору року?"
# Для служб раннього попередження: одна дата -> рівень тривоги + чого чекати далі.
# =====================================================================
with tab_warn:
    first_day, last_day = activity.index.min().date(), activity.index.max().date()
    c1, c2 = st.columns([1, 2])
    # date_input — календарик для вибору дати; за замовчуванням останній день у даних ("зараз")
    as_of = c1.date_input("Дата", value=last_day, min_value=first_day, max_value=last_day, format="DD.MM.YYYY",
                          help="За замовчуванням — останній день у даних. Можна вибрати будь-яку дату в минулому, "
                               "щоб подивитись, що показала б система тоді (напр. 10.04.2020 — Чорнобильська зона).")
    window = c1.select_slider("Вікно, днів", [3, 7, 14, 30], value=7,
                              help="За скільки останніх днів рахуємо активність")
    ew = analytics.early_warning(activity, as_of, window=window)
    # Слайдер за замовчуванням ховає неповний останній рік, а "зараз" для служб попередження —
    # саме там. Підказуємо, що свіжіші дані є, щоб ніхто не прийняв минулий грудень за "сьогодні".
    data_last = points["date"].max()
    if data_last > activity.index.max():
        c1.caption(f"Дані є до {data_last:%d.%m.%Y} — посуньте правий край слайдера «Роки» на "
                   f"{data_last.year}, щоб побачити найсвіжіший стан.")
    if not ew:
        st.info("Недостатньо даних для порівняння: вікно не вміщується в дані або для цих днів "
                "немає інших років. Виберіть пізнішу дату або ширший діапазон років.")
    else:
        icon, meaning = analytics.LEVELS[ew["level"]]
        c2.markdown(f"## {icon} Рівень: {ew['level']}")
        c2.markdown(f"Останні {window} дн. до **{ew['as_of']:%d.%m.%Y}**: {meaning}.")
        # Прогноз сезону: >1.2 — зазвичай наростає, <0.8 — спадає, між ними — без різких змін
        t = ew["trend"]
        if t is not None:
            if t >= 1.2:
                c2.markdown(f"📈 За історією, у наступні 4 тижні горіння зазвичай **наростає** (~×{t:.1f}).")
            elif t <= 0.8:
                c2.markdown(f"📉 За історією, у наступні 4 тижні горіння зазвичай **спадає** (~×{t:.1f}).")
            else:
                c2.markdown("➡️ За історією, у наступні 4 тижні зазвичай без різких змін.")

        m1, m2, m3 = st.columns(3)
        m1.metric(f"Зараз (за {window} дн.)", f"{ew['current']:,.0f}")
        # delta — маленька стрілка під числом: на скільки зараз вище/нижче норми
        m2.metric("Норма для цих днів (медіана)", f"{ew['median']:,.0f}",
                  delta=f"{ew['current'] - ew['median']:+,.0f} від норми", delta_color="inverse")
        m3.metric("Зараз горить більше, ніж у", f"{ew['percentile']:.0f}% років")

        # Стовпчики: те саме вікно днів у кожному році; вибраний рік — іншим кольором
        by = ew["by_year"]
        colors = ["#d62728" if y == ew["as_of"].year else "#f4a582" for y in by.index]
        fig = go.Figure(go.Bar(x=by.index, y=by.values, marker_color=colors,
                               hovertemplate="%{x}: %{y:,.0f}<extra></extra>"))
        fig.add_hline(y=ew["median"], line_dash="dot", annotation_text="норма (медіана)")
        fig.add_hline(y=ew["p90"], line_dash="dash", line_color="#999", annotation_text="90% років нижче")
        fig.update_layout(title=f"Ті самі {window} дн. ({ew['as_of'] - pd.Timedelta(days=window - 1):%d.%m}–"
                                f"{ew['as_of']:%d.%m}) у кожному році",
                          xaxis_title="Рік", yaxis_title="Активність", height=340, margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig, width="stretch")
        st.caption("Норма рахується по вибраних слайдером роках, крім року самої дати. "
                   "Прогноз — це те, як сезон зазвичай змінювався в ці тижні в минулі роки, а не прогноз погоди.")

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
        # Для кожної пари (рік, тиждень) рахуємо дату першого дня тижня — людям зрозуміліше за номер тижня
        a["дата"] = [analytics.week_start(y, w).strftime("%d.%m.%Y") for y, w in zip(a.year, a.week)]
        # Одна таблиця і для показу, і для завантаження. rename — українські назви колонок;
        # [[...]] — вибір і порядок колонок; round(1) — 1 знак після коми
        table = (a.rename(columns={"year": "рік", "week": "тиждень", "value": "активність",
                                   "norm": "норма", "kind": "тип"})
                 [["рік", "тиждень", "дата", "активність", "норма", "z", "тип"]]
                 .round(1))
        st.dataframe(table, hide_index=True, width="stretch")
        st.download_button(
            "⬇️ Скачати CSV",
            data=export.to_excel_csv(table),  # без зайвого стовпця з номерами рядків (index=False)
            file_name="anomalies.csv",
            mime="text/csv",
            key="dl_anomalies",
        )
    # Стовпчики "активність за кожен рік" + пунктир медіани: видно аномальні роки цілком
    yearly = activity.groupby(activity.index.year).sum()  # групуємо дні за роком і сумуємо
    fig = px.bar(x=yearly.index, y=yearly.values, labels=dict(x="Рік", y="Активність за рік"),
                 color=yearly.values, color_continuous_scale="YlOrRd")
    fig.add_hline(y=yearly.median(), line_dash="dot", annotation_text="медіана")
    # separators=", " — дробова частина через кому, тисячі через пробіл (12 345,6), як прийнято в Україні
    fig.update_layout(height=320, margin=dict(l=0, r=0, t=30, b=0), coloraxis_showscale=False,
                      separators=", ")
    # Своя підказка при наведенні: без технічного "color=...", число з роздільником тисяч і 1 знаком після коми
    fig.update_traces(hovertemplate="Рік: %{x}<br>Активність за рік: %{y:,.1f}<extra></extra>")
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
        # Скільки днів горіла кожна клітинка у вибрані місяці (деталі й «чому квадрати» — у firecal/mapgrid.py)
        agg = mapgrid.burn_days(cells, months)
        # choropleth_map — кожна клітинка = квадрат зі своїм кольором на підкладці Carto (потрібен інтернет).
        # НЕ density_map: та додає плями, що накладаються, і при віддаленні заливає червоним усю країну.
        fig = px.choropleth_map(
            agg, geojson=mapgrid.cells_geojson(agg, cell_deg), locations="id", color="днів горіння",
            range_color=(0, mapgrid.color_cap(agg["днів горіння"])),  # шкала до 99-го перцентиля, не до максимуму
            color_continuous_scale="YlOrRd", opacity=0.8,
            center=dict(lat=(map_box[1] + map_box[3]) / 2, lon=(map_box[0] + map_box[2]) / 2),  # центр зони
            # чим ширша зона (у градусах довготи), тим дальше "відсуваємо" карту
            zoom=5 if map_box[2] - map_box[0] > 9 else 6 if map_box[2] - map_box[0] > 4 else 7,
            map_style="carto-positron",
        )
        # marker_line_width=0 — без контурів квадратів (інакше сітка ліній забиває кольори);
        # hovertemplate — підказка: дні горіння і центр клітинки замість технічного id
        fig.update_traces(marker_line_width=0, customdata=agg[["lat", "lon"]],
                          hovertemplate="%{z} днів горіння<br>%{customdata[0]:.2f}° пн. ш., "
                                        "%{customdata[1]:.2f}° сх. д.<extra></extra>")
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
   Тижні рахуються від 1 січня (52 на рік, останні 1–2 дні — у 52-му), щоб усі роки мали однакову сітку.
6. **Раннє попередження.** Активність за останні N днів порівнюємо з тими самими днями календаря
   в інші роки: вище 75% років — 🟡 підвищений, вище 90% — 🟠 високий, робастний z ≥ 2 — 🔴 дуже високий.
   Тенденція — як горіння зазвичай змінювалось у наступні 4 тижні в минулі роки.

Джерело даних: NASA FIRMS (MODIS C6.1, VIIRS S-NPP 375 м).
""")
