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

import hashlib                      # md5 — «відбиток» завантажених файлів для ключа кешу

import pandas as pd                 # таблиці (DataFrame) — основний інструмент роботи з даними
import plotly.express as px         # швидкі інтерактивні графіки "одним рядком"
import plotly.graph_objects as go   # "ручні" графіки, коли треба кілька ліній на одному
import streamlit as st              # веб-інтерфейс

# Наш власний код із папки firecal/ (див. опис структури вище)
from firecal import analytics, export, harmonize, load, mapgrid, regions, words

# ---------------------------------------------------------------------
# МОВА ІНТЕРФЕЙСУ: українська або англійська (перемикач угорі бічної панелі).
# Значення живе в st.session_state["lang"] — окремо для кожного відвідувача. НЕ глобальна змінна
# модуля: на сервері всі відвідувачі ділять один Python, і хтось один перемикав би мову всім.
# ---------------------------------------------------------------------
LANGS = ["Українська", "English"]
st.session_state.setdefault("lang", LANGS[0])
EN = st.session_state["lang"] == "English"  # True — показуємо все англійською


def T(uk: str, en: str) -> str:
    """Текст потрібною мовою: T("Роки", "Years"). Обидва варіанти стоять поруч — так легко
    перевірити переклад і не забути другу мову, коли міняєш текст."""
    return en if EN else uk


# Налаштування сторінки: назва вкладки браузера, іконка, широкий макет. Має бути ПЕРШИМ st-викликом.
st.set_page_config(page_title=T("Календар горіння", "Fire Calendar"), page_icon="🔥", layout="wide")

# Підписи місяців для графіків (індекс 0 = січень) — потрібною мовою
MONTHS = words.months(EN)
# Як plotly пише числа: перший символ — десятковий, другий — роздільник тисяч.
# Українською "12 345,6", англійською "12,345.6".
SEP = ".," if EN else ", "

# Єдина система кольорів на весь додаток — глядач запам'ятовує "VIIRS — фіолетовий" і впізнає його скрізь:
#   супутники — завжди однакові кольори; "узгоджено" (наш результат) — вогняний;
#   "зазвичай / норма" — сірий; "зараз / вибране / проблема" — акцент (темно-червоний).
# Кольори підібрані так, щоб читатися і на світлому, і на темному фоні.
C_MODIS = "#3b82f6"      # синій
C_VIIRS = "#8b5cf6"      # фіолетовий
C_HARM = "#f97316"       # помаранчевий — "обидва супутники, узгоджено"
C_NORM = "#9ca3af"       # сірий — "зазвичай"
C_ACCENT = "#dc2626"     # червоний — "зараз", критичне
# Кольори рівнів тривоги для «світлофора» на вкладці «Раннє попередження»
LEVEL_COLORS = {"низький": "#16a34a", "підвищений": "#eab308", "високий": "#f97316", "дуже високий": "#dc2626"}
# Шкала «мало → багато» для календаря й стовпчиків по роках. Це YlOrRd з plotly, але без найсвітлішого
# кольору #ffffcc: майже білі клітинки «виїдали» очі в світлій темі і світились на темній.
# Низ — приглушений пісочний, далі ті самі кольори YlOrRd (пари [частка шкали 0..1, колір]).
HEAT_SCALE = [[0.0, "#efdca6"], [0.15, "#fed976"], [0.3, "#feb24c"], [0.45, "#fd8d3c"],
              [0.6, "#fc4e2a"], [0.75, "#e31a1c"], [0.88, "#bd0026"], [1.0, "#800026"]]
# Назви рівнів тривоги англійською. Ключі (українські) — з analytics.LEVELS, їх не міняємо.
LEVEL_EN = {"низький": "low", "підвищений": "elevated", "високий": "high", "дуже високий": "very high"}
LEVEL_MEANING_EN = {"низький": "at or below what is usual for this time of year",
                    "підвищений": "higher than in most years at this time",
                    "високий": "among the highest 10% of years for this time",
                    "дуже високий": "far above anything usual for this time"}
# Підказка «?» біля кожної кнопки CSV: людям — Excel, CSV — для скриптів і програм
CSV_HELP = T("Для читання очима краще «Скачати Excel». CSV — для програм і скриптів: роздільник «;», "
             "десяткова кома (так його правильно відкриває український Excel).",
             "For reading, use “Download Excel”. CSV is for programs and scripts: separator “;”, "
             "decimal comma (so that Ukrainian Excel opens it correctly).")


# =====================================================================
# ДАНІ. Функції з @st.cache_data виконуються лише раз для однакових аргументів.
# =====================================================================
# ПАМ'ЯТЬ (важливо для сервера з 2 ГБ RAM). Точок ~1,9 млн. Пастки, на які ми наступили:
#   * st.cache_data на КОЖЕН виклик віддає нову копію результату (розпаковує з pickle) — тобто на
#     кожен клік ще одна копія всіх точок. Тому точки — у st.cache_resource: один спільний об'єкт
#     без копій. Його НЕ МОЖНА змінювати після повернення (фільтри в harmonize роблять нові таблиці).
#   * у кеша без max_entries записів скільки завгодно: кожна нова зона/налаштування лишали в пам'яті
#     ще одну копію відфільтрованих точок. Тепер конвеєр повертає лише маленькі підсумки + ліміт записів.
# Так сервер Streamlit займає кілька сотень МБ замість 1,1–1,6 ГБ.
@st.cache_resource(show_spinner=T("Читаю дані FIRMS…", "Reading FIRMS data…"), max_entries=3)
def get_points(uploaded: tuple[tuple[str, bytes], ...] = ()) -> tuple[pd.DataFrame, list[str], bool]:
    """Повертає (точки в єдиній схемі, помилки завантажених файлів, чи взяли дані з завантажених).

    uploaded — файли, які користувач завантажив через сайт: кортеж пар (ім'я, байти).
    Биті файли (картинка з іменем .csv, чужий CSV без потрібних колонок) не валять додаток:
    їх пропускаємо, а текст помилки повертаємо, щоб показати людині. Якщо добрих файлів
    немає взагалі (або нічого не завантажено) — читаємо вбудовані дані (data/raw/ або data/deploy/).
    """
    parts, errors = [], []
    for name, data in uploaded:
        try:
            parts.append(load.read_upload(name, data))
        except load.BadFileError as e:
            errors.append(str(e))  # str(помилка) — її текст, вже зрозумілий людині
    # для кожного файлу: байти -> таблиця -> єдина схема; потім склеюємо всі в одну без дублікатів
    pts = load.combine(parts) if parts else load.load_cached()
    # Колонки time, daynight потрібні лише для прибирання дублікатів (combine) —
    # далі їх ніхто не читає, тож викидаємо, щоб не тримати в пам'яті.
    # satellite лишаємо: за ним видно роки, коли літала лише Terra (поправка в harmonize.aqua_factors)
    pts = pts.drop(columns=["time", "daynight"], errors="ignore")
    if not pts.empty:
        # Один раз визначаємо область кожної точки (миттєво, див. regions.oblast_of) —
        # далі фільтр за областями просто дивиться в цю колонку.
        # category — кожна назва області зберігається один раз, а в рядках лише її номер (у рази менше пам'яті)
        pts["oblast"] = regions.oblast_category(pts["lat"], pts["lon"])
        pts["sensor"] = pts["sensor"].astype("category")  # лише "MODIS" / "VIIRS"
        if "satellite" in pts:
            pts["satellite"] = pts["satellite"].astype("category")  # Terra / Aqua / N — кілька назв, мало пам'яті
    return pts, errors, bool(parts)


@st.cache_data(show_spinner=T("Гармонізую MODIS і VIIRS…", "Harmonizing MODIS and VIIRS…"), max_entries=24)
def run_pipeline(_points: pd.DataFrame, data_key, bbox, start, end, min_conf, cell_deg, oblasts, exclude_war):
    """Увесь конвеєр обробки: точки -> відбір -> клітинки -> денні суми -> гармонізований ряд.

    _points — з підкресленням: Streamlit НЕ хешує такий аргумент (хешувати 1,9 млн рядків на кожен клік
    дорого). Замість нього в ключ кешу йде data_key — «відбиток» набору даних (які файли завантажено).

    Повертає:
      summary     — маленькі підсумки по точках після фільтрів (самі точки не тримаємо — див. ПАМ'ЯТЬ):
                    n (скільки точок), box (прямокутник навколо них), by_year і by_day (точки за
                    роками / днями для кожного датчика — для вкладки «MODIS vs VIIRS»)
      cells       — пожежо-клітинко-дні (одна строка = одна клітинка сітки, що горіла в один день)
      daily       — таблиця по днях: скільки клітинок бачив MODIS, VIIRS, обидва, разом (union)
      activity    — ГОЛОВНИЙ результат: єдиний ряд активності горіння по днях
      k           — коефіцієнти калібрування для кожного місяця
      viirs_start — дата, з якої в даних з'являється VIIRS

    Сама логіка — у firecal/harmonize.py (там і пояснення, чому k рахується на всіх роках).
    Тут лише кеш: @st.cache_data не перераховує, якщо параметри ті самі.
    """
    p, cells, daily, activity, k, viirs_start = harmonize.run_pipeline(
        _points, bbox, start, end, min_conf=min_conf, cell_deg=cell_deg, oblasts=oblasts, exclude_war=exclude_war)
    # groupby(рік, датчик).size() — скільки точок; unstack — датчики в колонки (MODIS, VIIRS).
    # observed=True — лише датчики, що є в даних (sensor тепер category).
    summary = {
        "n": len(p),
        "box": (p["lon"].min(), p["lat"].min(), p["lon"].max(), p["lat"].max()) if len(p) else None,
        "by_year": p.groupby([p["date"].dt.year, "sensor"], observed=True).size().unstack(fill_value=0),
        "by_day": p.groupby([p["date"].dt.normalize(), "sensor"], observed=True).size().unstack(fill_value=0),
    }
    return summary, cells, daily, activity, k, viirs_start


# =====================================================================
# БІЧНА ПАНЕЛЬ: користувач вибирає зону інтересу і параметри.
# Кожен віджет просто ПОВЕРТАЄ вибране значення у змінну.
# =====================================================================
# Перемикач мови — найпершим: журі може бути міжнародним. Без value=: значення вже в session_state
# (setdefault на початку файлу). Кнопка «Скинути налаштування» мову НЕ скидає — це не налаштування даних.
st.sidebar.radio("Мова / Language", LANGS, key="lang", horizontal=True, label_visibility="collapsed")
st.sidebar.title(T("🔥 Календар горіння", "🔥 Fire Calendar"))

# На екрані зверху — основне (зона, роки), а рідкісні налаштування — у закритому блоці «Додатково».
# Але ЧИТАТИ свої CSV треба першими: від даних залежать межі слайдера років. Тому спершу створюємо
# два порожні «місця» в панелі (вони стоять там, де їх створили), а заповнюємо їх уже в потрібному порядку.
main_box = st.sidebar.container()                         # основні налаштування
adv = st.sidebar.expander(T("⚙️ Додатково", "⚙️ Advanced"), expanded=False)  # закритий блок


# Початкові значення віджетів бічної панелі (за їхніми key). Значення віджетам даємо САМЕ звідси
# (через session_state), а не параметром value=: так кнопка «Скинути налаштування» може їх повернути.
SIDEBAR_DEFAULTS = {
    "zone": list(regions.ZONES)[0],  # «Україна (вся)»
    "lon0": 30.0, "lon1": 34.0, "lat0": 46.0, "lat1": 48.0,
    "exclude_war": False,
    "min_conf": "nominal",
    "cell_deg": 0.1,
}
# setdefault — записати, лише якщо ключа ще нема (тобто при першому відкритті чи після скидання)
for _k, _v in SIDEBAR_DEFAULTS.items():
    st.session_state.setdefault(_k, _v)
# Номер скидання: додаємо його до key віджетів на вкладках (див. tab_key). Новий номер = нові віджети,
# і вони малюються з початковими значеннями.
st.session_state.setdefault("reset_n", 0)


def tab_key(name: str) -> str:
    """Ключ віджета на вкладці з номером скидання і мовою: "cal_freq" -> "cal_freq_0_English".

    Новий номер скидання = нові віджети з початковими значеннями. Мова в ключі — бо Streamlit не вміє
    поміняти підписи вже намальованого віджета: після перемикання мови радіокнопки лишались без вибору.
    Тож при зміні мови віджети на вкладках малюються наново (з початковими значеннями).
    """
    return f"{name}_{st.session_state['reset_n']}_{st.session_state['lang']}"


def lang_key(name: str) -> str:
    """Ключ віджета бічної панелі для поточної мови, БЕЗ втрати значення при перемиканні мови.

    Пастка: якщо підписи віджета змінились (інша мова), а key той самий, Streamlit на екрані
    лишає старі підписи, а select_slider взагалі скидався на перше значення («~5 км», «низька»)
    — тобто перемикач мови мовчки міняв дані. Тому:
      * справжнє значення живе в st.session_state[name] (напр. "cell_deg") — його ставлять
        SIDEBAR_DEFAULTS, кнопка «Скинути» і кнопки-приклади;
      * віджет має ключ з мовою ("cell_deg_English"); перед малюванням кладемо в нього значення,
        а після зміни людиною callback sync_back копіює його назад у name.
    """
    wk = f"{name}_{st.session_state['lang']}"
    st.session_state[wk] = st.session_state[name]
    return wk


def sync_back(name: str) -> None:
    """on_change віджета з lang_key: нове значення віджета -> у справжнє st.session_state[name]."""
    st.session_state[name] = st.session_state[f"{name}_{st.session_state['lang']}"]


def reset_settings() -> None:
    """Кнопка «Скинути налаштування»: усе як при першому відкритті сайту (зручно на демо).

    st.session_state — пам'ять віджетів між перезапусками скрипта. Виконується як on_click — ДО
    перезапуску, тож сторінка одразу малюється зі скинутими значеннями.
    Чому не просто st.session_state.clear(): тоді Python скидається, а віджет на екрані — ні
    (перевірено: список зон і далі показував «Свої координати», хоча дані були вже по всій Україні).
    Явно записане значення Streamlit на екрані оновлює. Завантажені файли кнопка не прибирає.
    """
    for k, v in SIDEBAR_DEFAULTS.items():
        st.session_state[k] = v
    st.session_state.pop("years", None)    # роки за замовчуванням поставить код біля слайдера
    # Мову НЕ скидаємо: кнопка про налаштування даних, а не про мову людини
    st.session_state.pop("ew_date", None)  # дата раннього попередження -> останній день у даних
    st.session_state["reset_n"] += 1       # усі віджети на вкладках — з початковими значеннями


# Кнопку створюємо тут, до будь-якого st.stop() нижче: навіть якщо людина ввела хибні координати,
# скинути все одним кліком можна. На екрані вона стоїть під «Додатково».
st.sidebar.button(T("↺ Скинути налаштування", "↺ Reset settings"), on_click=reset_settings, width="stretch",
                  help=T("Повернути зону, роки й усі інші налаштування до початкових.",
                         "Return the zone, years and all other settings to their defaults."))

# Завантаження власних CSV — у «Додатково»: на демо ним рідко користуються
files = adv.file_uploader(T("Свої дані FIRMS (CSV, MODIS і/або VIIRS)", "Your own FIRMS data (CSV, MODIS and/or VIIRS)"),
                          type="csv", accept_multiple_files=True,
                          # Чесно кажемо людині, що буде з файлом (так і є: див. get_points і docs/DEPLOY.md)
                          help=T("Файл не записується на диск і не видний іншим відвідувачам. Він обробляється в "
                                 "пам'яті сервера; там тримаються лише 3 останні набори, і все зникає при "
                                 "перезапуску. До 30 МБ на файл.",
                                 "The file is not written to disk and other visitors cannot see it. It is processed "
                                 "in server memory, which keeps only the last 3 data sets, and everything is gone "
                                 "after a restart. Up to 30 MB per file."))
# Перетворюємо файли в кортеж (ім'я, байти) — такий формат кеш уміє порівнювати
uploaded = tuple((f.name, f.getvalue()) for f in files) if files else ()
points, upload_errors, from_upload = get_points(uploaded)
# Биті файли — акуратне повідомлення замість трейсбеку на весь екран
for err in upload_errors:
    st.sidebar.error(err)
if upload_errors and not from_upload:
    st.sidebar.info(T("Жоден завантажений файл не підійшов — показую вбудовані дані.",
                      "None of the uploaded files fit — showing the built-in data."))

# Якщо даних немає взагалі — показуємо підказку і зупиняємо скрипт (st.stop)
if points.empty:
    st.warning(T("Немає даних. Покладіть CSV з FIRMS у `data/raw/` або запустіть "
                 "`python scripts/make_sample_data.py` для тестових даних.",
                 "No data. Put FIRMS CSV files into `data/raw/` or run "
                 "`python scripts/make_sample_data.py` for test data."))
    st.stop()

# Вибір зони: група областей, окрема область (справжні межі, regions.ZONES) або свої координати
# key="zone" — значення живе в st.session_state["zone"]: так кнопки-приклади на вкладці
# «Раннє попередження» можуть самі перемкнути зону (див. set_example нижче)
CUSTOM_ZONE = "Свої координати"  # пункт меню «зона = прямокутник з довготи/широти, які ввела людина»

def zone_name(z: str) -> str:
    """Назва зони для екрана потрібною мовою (значення в session_state лишається українським)."""
    return T(CUSTOM_ZONE, "Custom coordinates") if z == CUSTOM_ZONE else regions.zone_label(z, EN)


# format_func — що ПОКАЗАТИ в списку; у змінну region повертається сам ключ (українська назва)
region = main_box.selectbox(T("Зона інтересу", "Area of interest"), list(regions.ZONES) + [CUSTOM_ZONE],
                            key=lang_key("zone"), on_change=sync_back, args=("zone",), format_func=zone_name,
                            help=T("Дані — лише по Україні. Межі областей — справжні (з точністю ~1 км), "
                                   "а не прямокутники.",
                                   "Data covers Ukraine only. Oblast borders are real (~1 km precision), "
                                   "not rectangles."))
region_ui = zone_name(region)  # назва зони на екрані (банер, підписи, Excel)
oblasts = None  # None = без фільтра за областями
if region == CUSTOM_ZONE:
    c1, c2 = main_box.columns(2)  # дві колонки поруч: "від" і "до"
    # step=0.5 — на скільки змінюють кнопки «+/−»; від'ємні значення дозволені.
    # min_value/max_value НЕ ставимо: тоді Streamlit сам показує англійське «Number is outside the
    # allowed range». Межі перевіряємо нижче самі — з поясненням українською.
    lon0 = c1.number_input(T("Довгота від", "Longitude from"), step=0.5, key=lang_key("lon0"),
                           on_change=sync_back, args=("lon0",))
    lon1 = c2.number_input(T("Довгота до", "Longitude to"), step=0.5, key=lang_key("lon1"),
                           on_change=sync_back, args=("lon1",))
    lat0 = c1.number_input(T("Широта від", "Latitude from"), step=0.5, key=lang_key("lat0"),
                           on_change=sync_back, args=("lat0",))
    lat1 = c2.number_input(T("Широта до", "Latitude to"), step=0.5, key=lang_key("lat1"),
                           on_change=sync_back, args=("lat1",))
    main_box.caption(T("Україна — це приблизно довгота 22–40, широта 44–52.",
                       "Ukraine is roughly longitude 22–40, latitude 44–52."))
    # Перевірка координат. Якщо щось не так — показуємо, що саме, і зупиняємо скрипт: усі вкладки
    # без зони все одно порожні (st.stop тут, ДО вкладок, — безпечно). Без цих меж 10-значне число
    # відправляло центр карти в нікуди і браузер зависав (баг-звіт #21).
    problems = []  # список зрозумілих людині описів помилок
    if not (-180 <= lon0 <= 180 and -180 <= lon1 <= 180):
        problems.append(T("довгота має бути від −180 до 180", "longitude must be between −180 and 180"))
    if not (-90 <= lat0 <= 90 and -90 <= lat1 <= 90):
        problems.append(T("широта має бути від −90 до 90", "latitude must be between −90 and 90"))
    if lon0 >= lon1:
        problems.append(T("«Довгота від» має бути менше за «Довгота до»", "“Longitude from” must be less than “Longitude to”"))
    if lat0 >= lat1:
        problems.append(T("«Широта від» має бути менше за «Широта до»", "“Latitude from” must be less than “Latitude to”"))
    if problems:
        st.error(T("**Перевірте координати:** ", "**Check the coordinates:** ") + "; ".join(problems) + ".")
        st.stop()
    bbox = (lon0, lat0, lon1, lat1)  # bbox = "bounding box", прямокутник (захід, південь, схід, північ)
    map_box = bbox
else:
    oblasts = regions.ZONES[region]
    bbox = None                         # фільтруємо за областями, а не прямокутником
    map_box = regions.bbox_of(oblasts)  # прямокутник навколо областей — лише щоб відцентрувати карту

# Перемикач "без зони бойових дій": з 24.02.2022 у прифронтових областях багато хот-спотів
# від обстрілів, а не від с/г палів — вони спотворюють календар і аномалії
exclude_war = main_box.checkbox(
    T("Без прифронтових областей з 24.02.2022", "Exclude frontline oblasts since 24.02.2022"),
    key=lang_key("exclude_war"), on_change=sync_back, args=("exclude_war",),
    help=T("Викидає точки з областей " + ", ".join(regions.FRONTLINE) + " після початку повномасштабної "
           "війни: там багато хот-спотів від обстрілів, а не від пожеж рослинності.",
           "Drops points from " + ", ".join(regions.oblast_label(n, True) for n in regions.FRONTLINE)
           + " oblasts after the full-scale invasion began: many hotspots there come from shelling, "
             "not vegetation fires."))

# Слайдер років: межі беремо з самих даних; третій аргумент — діапазон за замовчуванням.
# default_years відкидає неповні роки на краях (напр. 2000, де MODIS лише з листопада).
y_min, y_max = int(points["date"].dt.year.min()), int(points["date"].dt.year.max())
if y_min == y_max:
    # Дані лише за один рік (напр. завантажили один річний файл FIRMS): слайдер з однаковими
    # краями Streamlit не малює — падає з StreamlitInvalidMinMaxError. Тож просто пишемо рік.
    years = (y_min, y_max)
    main_box.caption(T(f"Роки: у даних лише {y_min}. Норму й аномалії рахувати нема з чим — "
                       "для них потрібні кілька років.",
                       f"Years: the data has only {y_min}. Normal levels and anomalies need several years."))
else:
    # Значення слайдера живе в st.session_state["years"] (key="years"), щоб кнопки-приклади могли
    # його посунути. Початкове значення кладемо туди самі (а не параметром value=): Streamlit
    # сварится, якщо задати і value=, і session_state. Якщо збережені роки виходять за межі даних
    # (напр. завантажили інші файли) — повертаємо значення за замовчуванням, інакше слайдер упаде.
    saved = st.session_state.get("years")
    if saved is None or saved[0] < y_min or saved[1] > y_max:
        st.session_state["years"] = analytics.default_years(points["date"])
    years = main_box.slider(T("Роки", "Years"), y_min, y_max, key=lang_key("years"),
                            on_change=sync_back, args=("years",),
                            help=T("Неповні роки на краях даних (напр. 2000 — MODIS лише з листопада) "
                                   "за замовчуванням вимкнені, бо виглядають як провал.",
                                   "Incomplete years at the edges of the data (e.g. 2000 — MODIS only from November) "
                                   "are off by default because they look like a dip."))
# Достовірність точок: low пропускає все, high — лише найнадійніші. На екрані — українські слова
# (format_func показує "середня" замість "nominal"), а в код повертається технічне значення.
min_conf = adv.select_slider(T("Достовірність точок, не нижче", "Point confidence, at least"),
                             ["low", "nominal", "high"], key=lang_key("min_conf"),
                             on_change=sync_back, args=("min_conf",),
                             format_func=lambda c: words.conf_label(c, EN),
                             help=T("Супутник позначає кожну точку: низька / середня / висока достовірність. "
                                    "«Середня» відкидає найсумнівніші точки (науково: low / nominal / high).",
                                    "The satellite marks every point with low / nominal / high confidence. "
                                    "“Nominal” drops the most doubtful points."))
# Розмір ділянки сітки в градусах (0.1° ≈ 8×11 км в Україні). Менше = детальніше, але шумніше.
cell_deg = adv.select_slider(T("Розмір ділянки сітки", "Grid cell size"), [0.05, 0.1, 0.25, 0.5],
                             key=lang_key("cell_deg"), on_change=sync_back, args=("cell_deg",),
                             format_func=lambda d: words.cell_label(d, EN),
                             help=T("Точки супутників збираємо в ділянки цього розміру. Одна ділянка, де за день "
                                    "був вогонь, — це один «осередок горіння». Менше — детальніше, але шумніше.",
                                    "Satellite points are grouped into grid cells of this size. One cell with fire "
                                    "on a given day is one “fire cell”. Smaller is more detailed but noisier."))

# Попередження, якщо в data/raw/ лежать синтетичні файли (їхні назви починаються з SAMPLE)
if any(n.startswith("SAMPLE") for n in [f.name for f in load.RAW_DIR.glob("*.csv")]) and not from_upload:
    st.sidebar.error(T("⚠️ Зараз показано СИНТЕТИЧНІ тестові дані. Замініть їх справжніми з FIRMS.",
                       "⚠️ SYNTHETIC test data is shown. Replace it with real FIRMS data."))

# Роки для підписів: "2020", а не "2020–2020", якщо рік один
years_label = str(years[0]) if years[0] == years[1] else f"{years[0]}–{years[1]}"

# Перетворюємо вибрані роки в дати: з 1 січня першого року до 31 грудня останнього
start, end = pd.Timestamp(years[0], 1, 1), pd.Timestamp(years[1], 12, 31)
# Запускаємо обробку (з кешу, якщо параметри не змінились)
# tuple(oblasts) — кеш Streamlit краще працює з кортежами, ніж зі списками
# data_key — «відбиток» набору даних для ключа кешу: які файли завантажено (ім'я + md5 вмісту).
# () — вбудовані дані. Самі точки кеш не хешує (див. run_pipeline).
data_key = tuple((n, hashlib.md5(b).hexdigest()) for n, b in uploaded) if from_upload else ()
pts_sum, cells, daily, activity, k, viirs_start = run_pipeline(
    points, data_key, bbox, start, end, min_conf, cell_deg, tuple(oblasts) if oblasts else None, exclude_war)

# Після фільтрів не лишилось жодної точки — показуємо ОДНЕ зрозуміле повідомлення замість порожніх
# вкладок. Інакше раннє попередження писало зелене «🟢 низький рівень», хоча насправді даних немає:
# «пожеж нема» і «ми туди не дивились» — різні речі. st.stop() тут, ДО вкладок, — безпечно.
if pts_sum["n"] == 0:
    # Чи перетинається прямокутник людини з місцем, де взагалі є наші дані?
    outside = bbox is not None and (bbox[2] < points["lon"].min() or bbox[0] > points["lon"].max()
                                    or bbox[3] < points["lat"].min() or bbox[1] > points["lat"].max())
    if outside:
        st.warning(T("**У цій зоні немає даних.** Наші дані покривають лише Україну (приблизно довгота 22–40, "
                     "широта 44–52) — тут «немає пожеж» означало б «ми туди не дивились». Змініть координати.",
                     "**No data in this area.** Our data covers Ukraine only (roughly longitude 22–40, "
                     "latitude 44–52) — “no fires” here would really mean “we didn't look”. Change the coordinates."))
    else:
        st.warning(T("**Після фільтрів не лишилось жодної точки.** Спробуйте більше років, більшу зону "
                     "або нижчу достовірність у «⚙️ Додатково».",
                     "**No points left after the filters.** Try more years, a larger area "
                     "or lower confidence in “⚙️ Advanced”."))
    st.stop()
if bbox is not None:
    # Свої координати можуть бути набагато ширші за Україну (напр. вся Європа) — центруємо карту
    # не на прямокутнику людини, а на пожежах, що в нього потрапили
    map_box = pts_sum["box"]

# =====================================================================
# ШАПКА: заголовок і 4 ключові цифри (st.metric = велике число з підписом)
# =====================================================================
# Трохи власного CSS — щоб сторінка виглядала як продуманий сайт, а не як "скрипт на Streamlit".
# Кольори — напівпрозорі (rgba), тож однаково добре виглядають і в світлій, і в темній темі.
# data-testid — внутрішні назви блоків Streamlit; якщо після оновлення Streamlit щось "з'їде" — дивитись тут.
st.markdown("""
<style>
/* Менше порожнього місця згори сторінки */
/* 3.5rem — щоб банер не підлазив під верхню панель Streamlit (меню ⋮) */
[data-testid="stMainBlockContainer"] { padding-top: 3.5rem; }
/* Банер-заголовок. Градієнт НЕ згасає до нуля, а рамка достатньо помітна: інакше в світлій темі
   верх і правий край банера зливаються з білим фоном і плашка виглядає «обрізаною». */
.hero { padding: 18px 22px; border-radius: 14px; margin-bottom: 6px;
        background: linear-gradient(110deg, rgba(249,115,22,.20), rgba(220,38,38,.10) 60%, rgba(249,115,22,.06));
        border: 1px solid rgba(249,115,22,.45); }
.hero-kicker { font-size: .85rem; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; opacity: .75; }
.hero-title { font-size: 2.1rem; font-weight: 700; line-height: 1.15; margin: 2px 0 4px; }
.hero-title small { font-size: 1rem; font-weight: 500; opacity: .7; }
.hero-sub { font-size: 1rem; opacity: .85; }
/* Картки шапки — плитки з рамкою */
[data-testid="stMetric"] { background: rgba(249,115,22,.06); border: 1px solid rgba(249,115,22,.22);
                           border-radius: 12px; padding: 12px 16px; }
/* Число в картці: менше на вузьких екранах, інакше «mar–apr & aug–sep» обрізалось до «aug–s…» */
/* !important — у Streamlit свій розмір із сильнішим селектором; без нього правило не діяло */
[data-testid="stMetricValue"] { font-size: clamp(1.4rem, 1.8vw, 2.25rem) !important; }
[data-testid="stMetricValue"] * { white-space: normal !important; overflow: visible !important; }
/* Вкладки — трохи більший шрифт, щоб їх помічали */
[data-baseweb="tab"] p { font-size: 1rem; font-weight: 500; }
@media (max-width: 640px) { .hero-title { font-size: 1.5rem; }
                             /* на телефоні картки на всю ширину — число можна більше, ніж дає 1.8vw */
                             [data-testid="stMetricValue"] { font-size: 1.8rem !important; } }
/* Меню ⋮ — вбудоване в Streamlit, англійською, і перекласти його налаштуванням не можна.
   Тому: «Record screen» ховаємо (для журі зайве), а інші підписи підміняємо: справжній текст робимо
   нульового розміру (font-size: 0), а поруч CSS малює свій (::after з content). Іконкам повертаємо розмір. */
[data-testid="stMainMenuItem-recordScreencast"] { display: none !important; }
</style>
""", unsafe_allow_html=True)
# Підміна англійських підписів меню ⋮ — лише для української: англійською меню й так рідною мовою
if not EN:
    st.markdown("""
<style>
[data-testid^="stMainMenuItem-theme-"] { font-size: 0 !important; }
[data-testid^="stMainMenuItem-theme-"] [data-testid="stIconMaterial"] { font-size: 1.25rem; }
[data-testid^="stMainMenuItem-theme-"]::after { font-size: .75rem; }
[data-testid="stMainMenuItem-theme-System"]::after { content: "Як у системі"; }
[data-testid="stMainMenuItem-theme-Light"]::after { content: "Світла"; }
[data-testid="stMainMenuItem-theme-Dark"]::after { content: "Темна"; }
[data-testid="stMainMenuItem-print"] [data-testid="stMainMenuItemLabel"] { font-size: 0; }
[data-testid="stMainMenuItem-print"] [data-testid="stMainMenuItemLabel"]::after { content: "Друк"; font-size: .875rem; }
</style>
""", unsafe_allow_html=True)

# Банер: велика назва зони (без довгого переліку областей у дужках — він стоїть дрібно поруч)
zone_main, _, zone_rest = region_ui.partition(" (")  # "Полісся (Житомирська, ...)" -> "Полісся", "Житомирська, ...)"
zone_small = f" <small>({zone_rest}</small>" if zone_rest else ""
st.markdown(
    f'<div class="hero"><div class="hero-kicker">{T("🔥 Календар горіння", "🔥 Fire activity calendar")}</div>'
    f'<div class="hero-title">{zone_main}{zone_small}</div>'
    f'<div class="hero-sub">'
    + T("Активні пожежі із супутників NASA MODIS і VIIRS, узгоджені в один календар: "
        "коли зазвичай горить, коли було незвично і що відбувається зараз.",
        "Active fires from NASA MODIS and VIIRS satellites, harmonized into one calendar: "
        "when it usually burns, when it was unusual, and what is happening now.")
    + '</div></div>',
    unsafe_allow_html=True)
# Один рядок-підсумок: що саме показано. Видно на всіх вкладках, не відкриваючи бічне меню.
st.caption(f"📍 **{region_ui}** · 📆 **{years_label}** · {T('ділянка сітки', 'grid cell')} "
           f"**{words.cell_label(cell_deg, EN)}** · {T('достовірність точок', 'point confidence')}: "
           f"**{words.conf_label(min_conf, EN)}** {T('і вище', 'or higher')}"
           + (T(" · без прифронтових областей з 24.02.2022", " · without frontline oblasts since 24.02.2022")
              if exclude_war else ""))
clim = analytics.climatology(activity)        # "норма" для кожного тижня року
anom = analytics.anomalies(activity)          # тижні, що сильно відхиляються від норми
crit = analytics.critical_periods(activity)   # найактивніші періоди року

# Чотири картки-висновки: кожна відповідає на людське питання — скільки? коли? коли було найгірше?
# і (головне для хакатону) що ми зробили з даними двох супутників.
yearly = analytics.yearly_totals(activity)    # сума осередків за кожен рік
hot = analytics.hottest_year(activity)        # (рік, у скільки разів більше за звичайний) або None
m1, m2, m3, m4 = st.columns(4)  # 4 колонки в ряд
m1.metric(T("Осередків горіння за рік, у середньому", "Fire cells per year, average"),
          words.fmt_big(yearly.mean(), EN) if len(yearly) else "—",
          help=T("Осередок горіння — ділянка ~10 км, де супутник у цей день бачив вогонь. "
                 "Одна пожежа на 3 дні й 2 ділянки = 6 осередків.",
                 "A fire cell is a ~10 km grid cell where a satellite saw fire on that day. "
                 "One fire over 3 days and 2 cells = 6 fire cells."))
m2.metric(T("Сезони горіння", "Fire seasons"), words.seasons_label(crit, en=EN),
          help=T("Коли зазвичай горить найбільше (два найбільші критичні періоди року). "
                 "Деталі — вкладка «Критичні періоди».",
                 "When it usually burns the most (the two biggest critical periods of the year). "
                 "Details — the “Critical periods” tab."))
# delta — маленький підпис зі стрілкою під числом. delta_color="inverse": "більше" — червоне (для пожеж це погано)
m3.metric(T("Найгарячіший рік", "Hottest year"), str(hot[0]) if hot else "—",
          (f"{words.fmt_times(hot[1], EN)} " + T("від звичайного року", "vs a typical year")) if hot else None,
          delta_color="inverse",
          help=T("Рік із найбільшою кількістю осередків. «Звичайний рік» — медіана за вибрані роки.",
                 "The year with the most fire cells. “Typical year” is the median of the selected years."))
m4.metric(T("Узгоджено 2 супутники", "2 satellites harmonized"),
          f"{words.fmt_big(pts_sum['n'], EN)} → {words.fmt_big(activity.sum(), EN)}",
          help=T("Скільки точок MODIS і VIIRS ми звели в один ряд осередків горіння за вибрані роки. "
                 "Як саме — вкладка «MODIS vs VIIRS».",
                 "How many MODIS and VIIRS points we merged into one series of fire cells for the selected years. "
                 "How — the “MODIS vs VIIRS” tab."))


def show_table(df: pd.DataFrame) -> None:
    """Таблиця на екрані з шириною колонок «по найдовшому тексту».

    НАВІЩО: на телефоні Streamlit сам стискає колонки, і дати обрізались («02.04.200…»),
    а заголовки — до половини. Тут кожній колонці даємо ширину в пікселях: довжина
    найдовшого рядка (заголовок або значення) × ~7.5 px (середня ширина літери шрифту Inter) + відступи.
    Якщо колонки разом ширші за екран — таблиця гортається вбік (пальцем), нічого не обрізається.
    На широкому екрані width="stretch" однаково розтягує таблицю на всю ширину.
    """
    widths = {}
    for col in df.columns:
        # astype(str) — кожне значення як текст; .str.len().max() — довжина найдовшого
        longest = max(len(str(col)), int(df[col].astype(str).str.len().max()) if len(df) else 0)
        widths[col] = st.column_config.Column(width=int(longest * 7.5) + 28)
    st.dataframe(df, hide_index=True, width="stretch", column_config=widths)


def how_to_read(main: str, rest: str = "", details: str | None = None) -> None:
    """Рядок «💡 Як читати» на початку вкладки: жирне — головне, далі — коротке пояснення.

    details — подробиці для допитливих (як пораховано, наукові терміни) у закритому блоці,
    щоб не захаращувати екран. st.expander — блок, що розгортається кліком.
    Тексти передаємо вже потрібною мовою (через T).
    """
    st.markdown(f"💡 **{main}** {rest}")
    if details:
        with st.expander(T("Як це пораховано?", "How is this calculated?")):
            st.markdown(details)

# Опис поточних налаштувань — для шапки Excel-файлів: хто відкриє файл без додатку,
# має бачити, звідки ці цифри. Словник зберігає порядок, у якому ми додаємо пари.
# Слова — як на екрані, а науковий термін чи технічне значення — у дужках (правило: Excel для людей).
export_info = {
    T("Зона", "Area"): region_ui if region != CUSTOM_ZONE else
        T(f"свої координати: довгота {bbox[0]}–{bbox[2]}, широта {bbox[1]}–{bbox[3]}",
          f"custom coordinates: longitude {bbox[0]}–{bbox[2]}, latitude {bbox[1]}–{bbox[3]}"),
    T("Роки", "Years"): years_label,
    T("Достовірність точок", "Point confidence"): T(f"{words.CONF_LABELS[min_conf]} і вище ({min_conf})",
                                                    f"{min_conf} or higher"),
    T("Розмір ділянки сітки", "Grid cell size"): f"{words.cell_label(cell_deg, EN)} ({cell_deg}°)",
    T("Прифронтові з 24.02.2022", "Frontline oblasts since 24.02.2022"):
        T("викинуті", "excluded") if exclude_war else T("враховані", "included"),
    T("Джерело", "Source"): T("NASA FIRMS: MODIS і VIIRS (активні осередки пожеж)",
                              "NASA FIRMS: MODIS and VIIRS (active fire hotspots)"),
    T("Сформовано", "Created"): pd.Timestamp.now().strftime("%d.%m.%Y %H:%M"),
}

# Вкладки. Усе, що всередині "with tab_x:", малюється на відповідній вкладці.
tab_cal, tab_warn, tab_crit, tab_anom, tab_map, tab_cmp, tab_about = st.tabs(
    [T("📅 Календар", "📅 Calendar"), T("🚨 Раннє попередження", "🚨 Early warning"),
     T("🔥 Критичні періоди", "🔥 Critical periods"), T("⚠️ Аномалії", "⚠️ Anomalies"), T("🗺️ Карта", "🗺️ Map"),
     "🛰️ MODIS vs VIIRS", T("ℹ️ Методика", "ℹ️ Methodology")])

# =====================================================================
# 1. КАЛЕНДАР — теплова карта "рік × тиждень". Головний візуал проєкту.
# =====================================================================
with tab_cal:
    how_to_read(T("Кожен рядок — рік, кожна клітинка — тиждень.", "Each row is a year, each cell is a week."),
                T("Темніше — більше горіло. Темні вертикальні смуги — сезони, що повторюються щороку; "
                  "темна пляма поза смугою — незвичний рік.",
                  "Darker means more fire. Dark vertical bands are seasons that repeat every year; "
                  "a dark spot outside a band is an unusual year."),
                T("Клітинка = скільки осередків горіння (ділянок ~10 км, де за день був вогонь) було за цей тиждень "
                  "чи місяць. Найтемніший колір — 97-й перцентиль і вище, щоб один рекордний тиждень не "
                  "«вибілив» решту. Тижні рахуються від 1 січня: 52 на рік, однакові в усі роки.",
                  "Cell = how many fire cells (~10 km grid cells with fire on a day) there were in that week "
                  "or month. The darkest colour is the 97th percentile and above, so that one record week does not "
                  "wash out the rest. Weeks are counted from 1 January: 52 per year, the same in all years."))
    c1, c2 = st.columns([3, 1])  # ліва колонка втричі ширша за праву
    # Код ряду -> (назва на екрані, ряд даних). Перемикач — головна демонстрація проєкту:
    # клацнеш «Лише VIIRS» — до 2012 порожньо; «Лише MODIS» — роки непорівнянні.
    # У радіо — КОДИ (format_func показує назву): так вибір не скидається, коли перемикаєш мову.
    SERIES = {
        "harmonized": (T("Обидва супутники, узгоджено", "Both satellites, harmonized"), activity),
        "modis": (T("Лише MODIS (з 2000)", "MODIS only (since 2000)"), daily["modis"]),
        "viirs": (T("Лише VIIRS (з 2012)", "VIIRS only (since 2012)"), daily["viirs"]),
    }
    series_code = c2.radio(T("Дані", "Data"), list(SERIES), key=tab_key("cal_series"),
                           format_func=lambda c: SERIES[c][0],
                           help=T("Перемкніть на один супутник, щоб побачити, чому без узгодження роки непорівнянні",
                                  "Switch to one satellite to see why years are not comparable without harmonization"))
    freq_code = c2.radio(T("Крок", "Step"), ["week", "month"], key=tab_key("cal_freq"),
                         format_func=lambda f: T("Тиждень", "Week") if f == "week" else T("Місяць", "Month"))
    series_name, series = SERIES[series_code]
    freq = T("Тиждень", "Week") if freq_code == "week" else T("Місяць", "Month")  # для шапки Excel
    per = T("за місяць", "per month") if freq_code == "month" else T("за тиждень", "per week")
    # Таблиця: рядки = роки, стовпці = тижні (або місяці), значення = осередки горіння
    mat = analytics.calendar_matrix(series, freq_code)
    # Верхня межа кольору = 97-й перцентиль: одна дуже аномальна клітинка (напр. 2022) не "вбиває"
    # контраст решти. quantile сам пропускає порожні клітинки (NaN).
    zmax = float(pd.Series(mat.values.ravel()).quantile(0.97))
    if not zmax > 0:  # захист: порожня таблиця дає NaN, а нульова активність не дає шкали
        zmax = 1.0
    tick_vals, tick_text = words.scale_ticks(zmax, EN)  # шкала: числа + «мало» внизу, «багато» вгорі
    if freq_code == "month":
        x, hover_when = MONTHS, "%{x}"
        custom = None
    else:
        # По X — номери тижнів, але підписуємо місяцями (людині «Кві» зрозуміліше за «тиждень 14»).
        # А в підказці — дата початку тижня: customdata — таблиця тих самих розмірів, що й z.
        # У кожній клітинці customdata — пара [дата початку тижня, номер тижня]. Номер беремо звідси, а
        # не через %{x}: на осі X підписи-місяці, і %{x} показав би «тиждень Лип» замість «тиждень 26».
        x, hover_when = list(mat.columns), T("тиждень %{customdata[1]} (з %{customdata[0]})",
                                             "week %{customdata[1]} (from %{customdata[0]})")
        custom = [[[analytics.week_start(y, w).strftime("%d.%m"), int(w)] for w in mat.columns] for y in mat.index]
    # go.Heatmap замість px.imshow, бо тільки він вміє xgap/ygap: проміжки між клітинками в пікселях
    fig = go.Figure(go.Heatmap(
        z=mat.values, x=x, customdata=custom,
        y=[str(y) for y in mat.index],  # перший рік знизу, як і було з origin="lower"
        colorscale=HEAT_SCALE, zmin=0, zmax=zmax,
        xgap=2, ygap=2,  # 2 пікселі між клітинками
        # Шкала кольорів — горизонтальна, ПІД графіком: вертикальна праворуч на телефоні забирала
        # майже половину ширини, і тижні ставали тонкими рисками. yref="container" + y=0 — притиснута до
        # самого низу полотна (у нижній відступ margin.b), тож не налазить на підписи місяців.
        colorbar=dict(title=dict(text=T(f"Осередків {per}", f"Fire cells {per}"), side="top"),
                      tickvals=tick_vals, ticktext=tick_text,
                      orientation="h", yref="container", y=0, yanchor="bottom", thickness=12, len=0.8,
                      ticklabeloverflow="allow"),
        # hoverongaps=False — без підказки над порожніми клітинками (неповні тижні краю даних),
        # інакше там видно "Рік 2025, тиждень 50: " без числа
        hoverongaps=False,
        # "осередків: N", а не "N осередків" — щоб не узгоджувати відмінок із числом (1 осередок, 3 осередки)
        hovertemplate="%{y}, " + hover_when + T(" — осередків: ", " — fire cells: ")
                      + "<b>%{z:,.0f}</b><extra></extra>",
    ))
    # Висота залежить від кількості років, щоб рядки не були занадто тонкими.
    fig.update_layout(
        # коротко: не обрізається на телефоні
        title=dict(text=T(f"Осередки горіння {per}", f"Fire cells {per}"), subtitle_text=years_label),
        xaxis_title=None, yaxis_title=None,  # і так ясно: зліва роки, знизу місяці
        separators=SEP,  # українською 12 345,6, англійською 12,345.6 (так само на інших графіках)
        # +80 пікселів і нижній відступ — місце для підписів місяців і шкали під графіком
        height=max(450, 22 * len(mat) + 220), margin=dict(l=0, r=0, t=70, b=90))
    if freq_code == "week":
        # Підписи осі X — назви місяців у тижнях, де місяць починається
        mt_weeks, _ = analytics.month_ticks()
        fig.update_xaxes(tickvals=mt_weeks, ticktext=MONTHS)
    # type="category" — роки як підписи, а не числа: інакше при одному-двох роках plotly малює
    # на осі "2 019,6" і "2 020,4" (таке саме на інших графіках по роках нижче)
    fig.update_yaxes(type="category")
    # Лінії-орієнтири під мишею (spikes): вертикальна до місяців унизу і горизонтальна до року зліва —
    # так видно, який це тиждень і рік, навіть у середині великої таблиці (баг-звіт Макса, #21).
    # spikemode="across" — лінія через увесь графік; spikesnap="cursor" — іде за мишею.
    fig.update_xaxes(showspikes=True, spikemode="across", spikesnap="cursor", spikethickness=1,
                     spikedash="solid", spikecolor=C_NORM)
    fig.update_yaxes(showspikes=True, spikemode="across", spikesnap="cursor", spikethickness=1,
                     spikedash="solid", spikecolor=C_NORM)
    c1.plotly_chart(fig, width="stretch")
    # Вибрано один супутник — пояснюємо висновок словами, щоб людина не мусила здогадуватись сама
    if series_code != "harmonized":
        r = harmonize.overlap_ratio(daily["modis"], daily["viirs"], viirs_start)
        if r:
            c1.warning(T(f"⚠️ **Без узгодження роки не можна порівнювати.** З {viirs_start:%Y} року VIIRS бачить "
                         f"у {words.times_words(r)} більше осередків, ніж MODIS, — тож рік до і після "
                         f"{viirs_start:%Y} виглядають по-різному лише через супутник. Тому за замовчуванням — "
                         "обидва супутники, узгоджено.",
                         f"⚠️ **Without harmonization, years cannot be compared.** Since {viirs_start:%Y} VIIRS sees "
                         f"{words.times_words(r, True)} more fire cells than MODIS, so years before and after "
                         f"{viirs_start:%Y} look different only because of the satellite. That is why the default "
                         "is both satellites, harmonized."))
    # Кнопка завантаження: віддаємо ту саму таблицю (рік × тиждень/місяць), що намальована як теплова карта.
    # calendar_for_export: роки підписані, стовпці — назви місяців або дати початку тижнів ("02.04").
    # Для CSV — завжди українські підписи (їх читають скрипти команди), для Excel — мовою екрана.
    mat_out = export.calendar_for_export(mat, freq_code, words.MONTHS)
    mat_xl = export.calendar_for_export(mat, freq_code, MONTHS).rename_axis(T("рік", "year"))
    # Код ряду (series_code) у назві файлу: скачавши узгоджений, MODIS і VIIRS, їх легко розрізнити
    fname = f"calendar_{freq_code}_{series_code}"
    # Шапка Excel: спільні налаштування + що саме в цій таблиці + підсумки.
    # {**a, **b} — новий словник з пар обох словників (порядок: спершу a, потім b).
    cal_info = {
        **export_info,
        T("Дані", "Data"): series_name + (
            T(f" (гармонізований ряд: до {viirs_start:%d.%m.%Y} — MODIS × поправка k за місяцем)",
              f" (harmonized series: before {viirs_start:%d.%m.%Y} — MODIS × monthly correction k)")
            if series_code == "harmonized" and viirs_start is not None else ""),
        T("Крок", "Step"): freq + (T(" (підпис — дата першого дня тижня)", " (label — date of the first day of the week)")
                                   if freq_code == "week" else ""),
        T("Що в клітинці", "Cell value"): T(
            "осередки горіння (пожежо-клітинко-дні): скільки разів ділянка сітки горіла "
            "за цей період, по одному на ділянку за день; порожньо — за цей період даних немає",
            "fire cells (fire-cell-days): how many times a grid cell burned in this period, "
            "one per cell per day; empty — no data for this period"),
        **export.calendar_summary(mat, list(mat_xl.columns), en=EN),
    }
    # Excel — для людей (шапка + кольори як на тепловій карті); CSV — для обробки в коді чи інших програмах
    c2.download_button(
        T("⬇️ Скачати Excel", "⬇️ Download Excel"),
        data=export.to_excel(mat_xl, index=True, heatmap=True, sheet=T("Календар", "Calendar"),  # index=True: роки лишаємо
                             title=T(f"Календар горіння — {region_ui}", f"Fire calendar — {region_ui}"), info=cal_info),
        file_name=f"{fname}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="dl_calendar_xlsx",  # унікальні ключі, щоб кнопки не конфліктували між собою
    )
    c2.download_button(
        T("⬇️ Скачати CSV (для програм)", "⬇️ Download CSV (for programs)"), help=CSV_HELP,
        data=export.to_excel_csv(mat_out, index=True),
        file_name=f"{fname}.csv",
        mime="text/csv",
        key="dl_calendar",
    )

# =====================================================================
# РАННЄ ПОПЕРЕДЖЕННЯ — "наскільки зараз горить більше, ніж зазвичай у цю пору року?"
# Для служб раннього попередження: одна дата -> рівень тривоги + чого чекати далі.
# =====================================================================
with tab_warn:
    how_to_read(T("Чи горить зараз більше, ніж зазвичай у цю пору року?",
                  "Is it burning more than usual for this time of year?"),
                T("Виберіть дату — побачите рівень тривоги і чого зазвичай чекати в наступні 4 тижні.",
                  "Pick a date to see the alert level and what usually happens in the next 4 weeks."),
                T("Беремо осередки горіння за останні N днів до дати і порівнюємо з тими самими днями в інші роки. "
                  "🟡 — більше, ніж у 75% років; 🟠 — більше, ніж у 90% років; 🔴 — далеко за межами звичайного "
                  "(науково: робастний z ≥ 2, медіана/MAD). «Зазвичай» — медіана по роках.",
                  "We take fire cells over the last N days up to the date and compare them with the same days in other "
                  "years. 🟡 — more than in 75% of years; 🟠 — more than in 90% of years; 🔴 — far outside the usual "
                  "(robust z ≥ 2, median/MAD). “Usual” is the median across years."))
    first_day, last_day = activity.index.min().date(), activity.index.max().date()
    c1, c2 = st.columns([1, 2])

    def set_example(zone: str, date: str) -> None:
        """Кнопка-приклад: ставить зону, дату і (якщо треба) розсуває слайдер років.

        Це callback (on_click): Streamlit викликає його ДО наступного проходу скрипта, тож нові значення
        в st.session_state встигають потрапити у віджети (зону й роки — через їхні key).
        """
        st.session_state["zone"] = zone
        st.session_state["ew_date"] = pd.Timestamp(date).date()
        y = pd.Timestamp(date).year
        saved = st.session_state.get("years")
        if saved and not (saved[0] <= y <= saved[1]):  # рік прикладу поза слайдером — розсуваємо його
            st.session_state["years"] = (min(saved[0], y), max(saved[1], y))

    # Готові сценарії для демо: один клік — і видно 🔴, без пошуку дати в календарику.
    # Обидва перевірені (див. AGENTS.md): Чорнобильська пожежа 2020 і весняні пали 2015.
    polissia = next(z for z in regions.ZONES if z.startswith("Полісся"))
    c1.caption(T("Приклади:", "Examples:"))
    b1, b2 = c1.columns(2)
    b1.button(T("🔥 Чорнобиль, квітень 2020", "🔥 Chornobyl, April 2020"), on_click=set_example,
              args=(polissia, "2020-04-10"), width="stretch")
    b2.button(T("🔥 Україна, березень 2015", "🔥 Ukraine, March 2015"), on_click=set_example,
              args=("Україна (вся)", "2015-03-20"), width="stretch")
    # Дата: за замовчуванням останній день у даних ("зараз") або дата з кнопки-прикладу.
    # Віджет без key: коли value змінюється (натиснули приклад), Streamlit показує нове значення.
    # Дата поза межами даних (змінили зону чи роки) — повертаємось до останнього дня, інакше віджет упаде.
    picked = st.session_state.get("ew_date")
    default_day = picked if picked and first_day <= picked <= last_day else last_day
    as_of = c1.date_input(T("Дата", "Date"), value=default_day, min_value=first_day, max_value=last_day,
                          format="DD.MM.YYYY",
                          help=T("За замовчуванням — останній день у даних. Можна вибрати будь-яку дату в минулому, "
                                 "щоб подивитись, що показала б система тоді.",
                                 "By default — the last day in the data. Pick any past date to see what the "
                                 "system would have shown then."))
    window = c1.select_slider(T("Вікно, днів", "Window, days"), [3, 7, 14, 30], value=7, key=tab_key("ew_window"),
                              help=T("За скільки останніх днів рахуємо осередки горіння",
                                     "How many recent days of fire cells we count"))
    ew = analytics.early_warning(activity, as_of, window=window)
    # Слайдер за замовчуванням ховає неповний останній рік, а "зараз" для служб попередження —
    # саме там. Підказуємо, що свіжіші дані є, щоб ніхто не прийняв минулий грудень за "сьогодні".
    data_last = points["date"].max()
    if data_last > activity.index.max():
        c1.caption(T(f"Дані є до {data_last:%d.%m.%Y} — посуньте правий край слайдера «Роки» на "
                     f"{data_last.year}, щоб побачити найсвіжіший стан.",
                     f"Data is available up to {data_last:%d.%m.%Y} — move the right end of the “Years” slider to "
                     f"{data_last.year} to see the latest state."))
    if not ew:
        st.info(T("Недостатньо даних для порівняння: вікно не вміщується в дані або для цих днів "
                  "немає інших років. Виберіть пізнішу дату або ширший діапазон років.",
                  "Not enough data to compare: the window does not fit into the data or there are no other years "
                  "for these days. Pick a later date or a wider range of years."))
    else:
        icon, meaning = analytics.LEVELS[ew["level"]]
        level_ui = LEVEL_EN[ew["level"]] if EN else ew["level"]       # назва рівня на екрані
        meaning = LEVEL_MEANING_EN[ew["level"]] if EN else meaning
        # «Світлофор»: плашка кольору рівня. Свій HTML (unsafe_allow_html=True), бо в Streamlit немає
        # помаранчевої плашки. Фон — той самий колір, але прозорий ("22" у кінці = ~13% непрозорості),
        # а текст успадковує колір теми — тож плашку добре видно і в світлій, і в темній темі.
        col = LEVEL_COLORS[ew["level"]]
        c2.markdown(
            f'<div style="border-left:6px solid {col};background:{col}22;padding:14px 18px;border-radius:8px">'
            f'<div style="font-size:1.7rem;font-weight:700;line-height:1.2">{icon} {level_ui.capitalize()}</div>'
            f'<div style="margin-top:6px;font-size:1.05rem">{words.warning_sentence(ew, window, EN)}</div>'
            f'<div style="margin-top:2px;opacity:.75">{T("Рівень означає", "Level means")}: {meaning}.</div></div>',
            unsafe_allow_html=True)
        # Чого чекати далі: >1.2 — зазвичай наростає, <0.8 — спадає, між ними — без різких змін
        t = ew["trend"]
        if t is not None:
            if t >= 1.2:
                c2.markdown(T(f"📈 Зазвичай у наступні 4 тижні горіння **наростає** (~{words.fmt_times(t)}).",
                              f"📈 Burning usually **increases** over the next 4 weeks (~{words.fmt_times(t, True)})."))
            elif t <= 0.8:
                c2.markdown(T(f"📉 Зазвичай у наступні 4 тижні горіння **спадає** (~{words.fmt_times(t)}).",
                              f"📉 Burning usually **decreases** over the next 4 weeks (~{words.fmt_times(t, True)})."))
            else:
                c2.markdown(T("➡️ Зазвичай у наступні 4 тижні без різких змін.",
                              "➡️ Usually no sharp changes over the next 4 weeks."))

        m1, m2, m3 = st.columns(3)
        m1.metric(T(f"Осередків за {window} дн.", f"Fire cells in {window} days"), words.fmt_num(ew["current"], 0, EN))
        # delta — маленька стрілка під числом: на скільки зараз більше/менше, ніж зазвичай.
        # Знак "+" ставимо самі (fmt_num його не пише): за знаком Streamlit вибирає стрілку вгору чи вниз.
        diff = ew["current"] - ew["median"]
        m2.metric(T("Зазвичай у ці дні", "Usual for these days"), words.fmt_num(ew["median"], 0, EN),
                  delta=("+" if diff >= 0 else "") + words.fmt_num(diff, 0, EN) + T(" від звичайного", " vs usual"),
                  delta_color="inverse")
        m3.metric(T("Горить більше, ніж у", "Burning more than in"),
                  T(f"{ew['percentile']:.0f}% інших років", f"{ew['percentile']:.0f}% of other years"))

        # Стовпчики: те саме вікно днів у кожному році; рік вибраної дати — акцентом, решта сірі
        by = ew["by_year"]
        colors = [C_ACCENT if y == ew["as_of"].year else C_NORM for y in by.index]
        fig = go.Figure(go.Bar(x=[str(y) for y in by.index], y=by.values, marker_color=colors,
                               hovertemplate="%{x}" + T(" — осередків: ", " — fire cells: ")
                                             + "<b>%{y:,.0f}</b><extra></extra>"))
        fig.add_hline(y=ew["median"], line_dash="dot", annotation_text=T("зазвичай", "usual"))
        fig.add_hline(y=ew["p90"], line_dash="dash", line_color=C_NORM,
                      annotation_text=T("у 9 з 10 років нижче", "lower in 9 of 10 years"))
        # Заголовок plotly не переноситься: довгий обрізався на телефоні. Тому коротко + subtitle
        # (другий рядок меншим шрифтом). Так само на інших графіках з довгими назвами.
        days = f"{ew['as_of'] - pd.Timedelta(days=window - 1):%d.%m}–{ew['as_of']:%d.%m}"
        fig.update_layout(title=dict(text=T(f"Ті самі {window} дн. у кожному році", f"The same {window} days in every year"),
                                     subtitle_text=T(f"{days}; {ew['as_of'].year} — червоним",
                                                     f"{days}; {ew['as_of'].year} in red")),
                          xaxis_type="category", separators=SEP,
                          yaxis_title=T("Осередків горіння", "Fire cells"), height=360, margin=dict(l=0, r=0, t=70, b=0))
        st.plotly_chart(fig, width="stretch")
        st.caption(T("«Зазвичай» — по вибраних слайдером роках, крім року самої дати. "
                     "«Чого чекати» — як сезон зазвичай змінювався в ці тижні в минулі роки, а не прогноз погоди.",
                     "“Usual” — over the years selected with the slider, except the year of the date itself. "
                     "The outlook shows how the season usually changed in these weeks in past years — "
                     "it is not a weather forecast."))

# =====================================================================
# 2. КРИТИЧНІ ПЕРІОДИ — "типовий рік": як змінюється горіння по тижнях
# =====================================================================
with tab_crit:
    how_to_read(T("Коли в цій зоні зазвичай горить найбільше.", "When this area usually burns the most."),
                T("Лінія — звичайний рік, червоні смуги — критичні періоди.",
                  "The line is a typical year, red bands are critical periods."),
                T("Для кожного тижня року беремо значення за всі вибрані роки. Лінія — медіана («зазвичай»), "
                  "заливка — до 90-го перцентиля (у 9 з 10 років горіло менше). Критичні періоди — 25% тижнів "
                  "із найбільшою середньою кількістю осередків, сусідні тижні склеєні.",
                  "For each week of the year we take the values of all selected years. The line is the median "
                  "(“usual”), the shading goes up to the 90th percentile (9 of 10 years burned less). Critical periods "
                  "are the 25% of weeks with the highest mean number of fire cells, neighbouring weeks merged."))
    fig = go.Figure()
    # Дата початку кожного тижня — для підказки ("тиждень з 02.04"); 2021 — довільний невисокосний рік
    wk_dates = [analytics.week_start(2021, w).strftime("%d.%m") for w in clim.index]
    # Трюк для заливки діапазону: спочатку невидима лінія "у 9 з 10 років нижче" (90-й перцентиль),
    # потім "зазвичай" (медіана) з fill="tonexty" — заливається простір між ними.
    fig.add_trace(go.Scatter(x=clim.index, y=clim["p90"], line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=clim.index, y=clim["median"], fill="tonexty", fillcolor=C_HARM + "33",
                             # <br> — перенос рядка в легенді: одним рядком текст обрізався на телефоні
                             name=T("Зазвичай; заливка — до<br>«у 9 з 10 років нижче»",
                                    "Usual; shading — up to<br>“lower in 9 of 10 years”"),
                             line=dict(color=C_HARM, width=3),
                             customdata=list(zip(wk_dates, clim["p90"])),
                             hovertemplate=T("Тиждень з %{customdata[0]}<br>зазвичай: <b>%{y:,.0f}</b><br>"
                                             "у 9 з 10 років нижче: %{customdata[1]:,.0f}<extra></extra>",
                                             "Week from %{customdata[0]}<br>usual: <b>%{y:,.0f}</b><br>"
                                             "lower in 9 of 10 years: %{customdata[1]:,.0f}<extra></extra>")))
    # Критичні періоди — напівпрозорі червоні смуги прямо на графіку з підписом "дати · % річного горіння".
    # add_vrect — прямокутник на всю висоту між x0 і x1; ±0.5 — щоб смуга покривала тижні цілком.
    for _, row in crit.iterrows():
        w0 = int(analytics.week_of_year(pd.DatetimeIndex([row["початок"]]))[0])
        w1 = int(analytics.week_of_year(pd.DatetimeIndex([row["кінець"]]))[0])
        fig.add_vrect(x0=w0 - 0.5, x1=w1 + 0.5, fillcolor=C_ACCENT, opacity=0.12, line_width=0,
                      annotation_text=f"{row['початок']:%d.%m}–{row['кінець']:%d.%m}<br>"
                                      f"<b>{words.fmt_num(row['частка річного горіння, %'], 0, EN)}%</b>"
                                      + T(" горіння", " of fires"),
                      annotation_position="top left", annotation_font_size=11)
    mt_weeks, _ = analytics.month_ticks()
    fig.update_layout(title=dict(text=T("Звичайний рік по тижнях", "Typical year by week"),
                                 subtitle_text=T("осередків горіння за тиждень", "fire cells per week")),
                      yaxis_title=None, height=440, separators=SEP, margin=dict(l=0, r=0, t=90, b=60),
                      # entrywidth — ширина запису легенди явно: plotly міряє текст ДО завантаження шрифту
                      # Inter і на телефоні обрізав кінець («…років ни»)
                      legend=dict(orientation="h", yref="container", y=0, yanchor="bottom", entrywidth=230),
                      xaxis=dict(tickvals=mt_weeks, ticktext=MONTHS, range=[0.5, 52.5]))
    st.plotly_chart(fig, width="stretch")
    # Таблиця — для тих, кому потрібні точні цифри; на екрані її заміняють смуги на графіку
    with st.expander(T("Таблиця критичних періодів", "Critical periods table")):
        crit_ui = pd.DataFrame({
            T("Період", "Period"): [f"{s:%d.%m} – {e:%d.%m}" for s, e in zip(crit["початок"], crit["кінець"])],
            # Короткі заголовки — щоб на телефоні всі три колонки влазили без гортання вбік
            # fmt_num — число мовою екрана (українською десяткова кома: «23,6», а не «23.6»)
            T("% за рік", "% of year"): [words.fmt_num(v, 1, EN) for v in crit["частка річного горіння, %"]],
            T("Пік (тиждень з)", "Peak week from"):
                [analytics.week_start(2021, w).strftime("%d.%m") for w in crit["пік (тиждень)"]],
        }) if not crit.empty else crit
        show_table(crit_ui)

# =====================================================================
# 3. АНОМАЛІЇ — конкретні тижні конкретних років, що вибиваються з норми
# =====================================================================
with tab_anom:
    how_to_read(T("Які роки й тижні були незвично гарячими.", "Which years and weeks were unusually hot."),
                T("Зверху — кожен рік проти звичайного, нижче — найнезвичніші тижні.",
                  "On top — every year against a typical one, below — the most unusual weeks."),
                T("Тиждень незвичний, якщо осередків горіння в ньому набагато більше (або менше), ніж зазвичай "
                  "у цей тиждень року. Науково: відхилення від медіани цього тижня більше ніж 2 робастні "
                  "стандартні відхилення (медіана/MAD, |z| > 2).",
                  "A week is unusual if it has far more (or fewer) fire cells than usual for that week of the year. "
                  "Scientifically: the deviation from the week's median exceeds 2 robust standard deviations "
                  "(median/MAD, |z| > 2)."))
    # Стовпчики "осередки за кожен рік" + пунктир медіани: видно аномальні роки цілком.
    # yearly — суми за роками, пораховані вище для шапки (analytics.yearly_totals)
    fig = px.bar(x=yearly.index, y=yearly.values,
                 labels=dict(x=T("Рік", "Year"), y=T("Осередків за рік", "Fire cells per year")),
                 color=yearly.values, color_continuous_scale=HEAT_SCALE,
                 title=T(f"Осередків горіння за рік, {years_label}", f"Fire cells per year, {years_label}"))
    fig.add_hline(y=yearly.median(), line_dash="dot",
                  annotation_text=T("звичайний рік (медіана)", "typical year (median)"))
    # separators — формат чисел мовою екрана (українською 12 345,6)
    fig.update_layout(height=320, margin=dict(l=0, r=0, t=50, b=0), coloraxis_showscale=False,
                      separators=SEP, xaxis_type="category")  # роки як підписи (див. календар)
    # Своя підказка при наведенні: без технічного "color=...", число з роздільником тисяч і 1 знаком після коми
    fig.update_traces(hovertemplate=T("Рік: %{x}<br>Осередків за рік: %{y:,.0f}<extra></extra>",
                                      "Year: %{x}<br>Fire cells per year: %{y:,.0f}<extra></extra>"))
    st.plotly_chart(fig, width="stretch")
    # Потім топ-10 найнезвичніших тижнів, повна таблиця в розгортному блоці і кнопки завантаження
    if anom.empty:
        st.info(T("Незвичних тижнів не знайдено.", "No unusual weeks found."))
    else:
        # --- Топ-10 гарячих тижнів (сортування за різницею в осередках — див. analytics.top_anomalies)
        top = analytics.top_anomalies(anom, 10)
        st.subheader(T(f"Топ-{len(top)} незвично гарячих тижнів", f"Top {len(top)} unusually hot weeks"))
        show_table(pd.DataFrame({
            T("Тиждень з", "Week from"): [analytics.week_start(y, w).strftime("%d.%m.%Y") for y, w in zip(top.year, top.week)],
            T("Осередків", "Fire cells"): top["value"].round().astype(int),
            T("Зазвичай", "Usual"): top["norm"].round().astype(int),
            T("У скільки разів більше", "Times more"): [words.ratio_text(v, n, EN) for v, n in zip(top["value"], top["norm"])],
        }))

        a = anom.copy()  # копія, щоб не зіпсувати закешовані дані
        # Для кожної пари (рік, тиждень) рахуємо дату першого дня тижня — людям зрозуміліше за номер тижня
        a["дата"] = [analytics.week_start(y, w).strftime("%d.%m.%Y") for y, w in zip(a.year, a.week)]
        # Таблиця для CSV — з НЕЗМІННИМИ назвами колонок: її читають скрипти команди (див. AGENTS.md).
        # rename — українські назви колонок; [[...]] — вибір і порядок колонок; round(1) — 1 знак після коми
        table = (a.rename(columns={"year": "рік", "week": "тиждень", "value": "активність",
                                   "norm": "норма", "kind": "тип"})
                 [["рік", "тиждень", "дата", "активність", "норма", "z", "тип"]]
                 .round(1))
        # Та сама таблиця для екрана й Excel — словами з екрана, плюс «у скільки разів» (z лишаємо для науковців)
        if EN:
            table_ui = table.rename(columns={"рік": "year", "тиждень": "week", "дата": "week from",
                                             "активність": "fire cells", "норма": "usual",
                                             "тип": "above or below usual"})
            table_ui["above or below usual"] = table_ui["above or below usual"].map(
                {"вище норми": "above usual", "нижче норми": "below usual"})
            table_ui.insert(5, "times", [words.ratio_text(v, n, True) for v, n in zip(a["value"], a["norm"])])
        else:
            table_ui = table.rename(columns={"дата": "тиждень з", "активність": "осередків", "норма": "зазвичай",
                                             "тип": "вище чи нижче норми"})
            table_ui.insert(5, "у скільки разів", [words.ratio_text(v, n) for v, n in zip(a["value"], a["norm"])])
        with st.expander(T(f"Усі незвичні тижні ({len(table)}), зокрема нижче норми",
                           f"All unusual weeks ({len(table)}), including below usual")):
            show_table(table_ui)
        # Дві кнопки в один рядок: st.columns ділить ширину навпіл
        b1, b2 = st.columns(2)
        n_hot = (table['тип'] == 'вище норми').sum()
        b1.download_button(
            T("⬇️ Скачати Excel", "⬇️ Download Excel"),
            # без кольорів: тут різні величини в колонках. Шапка — налаштування + як читати таблицю
            # Excel — мовою екрана (table_ui: «осередків», «зазвичай», «у скільки разів» + z для науковців)
            data=export.to_excel(table_ui, sheet=T("Аномалії", "Anomalies"),
                                 title=T(f"Незвичні тижні — {region_ui}", f"Unusual weeks — {region_ui}"), info={
                **export_info,
                T("Правило", "Rule"): T(
                    "тиждень незвичний, якщо осередків горіння в ньому набагато більше чи менше, ніж "
                    "зазвичай у цей тиждень року (науково: |z| > 2 — більше ніж 2 робастні стандартні "
                    "відхилення, медіана/MAD)",
                    "a week is unusual if it has far more or fewer fire cells than usual for that week of the year "
                    "(|z| > 2 — more than 2 robust standard deviations, median/MAD)"),
                T("Зазвичай", "Usual"): T("медіана осередків горіння цього тижня за всі роки",
                                          "median number of fire cells for this week across all years"),
                T("Знайдено", "Found"): T(f"{len(table)} тижнів, з них вище норми — {n_hot}",
                                          f"{len(table)} weeks, of which above usual — {n_hot}"),
            }),
            file_name="anomalies.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_anomalies_xlsx",
        )
        b2.download_button(
            T("⬇️ Скачати CSV (для програм)", "⬇️ Download CSV (for programs)"), help=CSV_HELP,
            data=export.to_excel_csv(table),  # без зайвого стовпця з номерами рядків (index=False)
            file_name="anomalies.csv",
            mime="text/csv",
            key="dl_anomalies",
        )

# =====================================================================
# 4. КАРТА — де горить найчастіше (теплова карта щільності)
# =====================================================================
with tab_map:
    how_to_read(T("Де горить найчастіше.", "Where it burns most often."),
                T("Яскравіше — частіше був вогонь. Наведіть мишу на місце, щоб побачити число днів.",
                  "Brighter means fire was seen more often. Hover over a place to see the number of days."),
                T("Для кожної ділянки ~10 км рахуємо, скільки днів за вибрані роки й місяці там був вогонь. "
                  "Тихі місця прозорі, щоб було видно лише гарячі; найяскравіший колір — 99-й перцентиль і вище. "
                  "При сильному наближенні картинка стає різкою — видно окремі ділянки.",
                  "For each ~10 km grid cell we count on how many days of the selected years and months it had fire. "
                  "Quiet places are transparent so only hot ones show; the brightest colour is the 99th percentile "
                  "and above. When zoomed in closely the picture becomes sharp — individual cells are visible."))
    # Сезони замість 12 чипів-місяців: одним кліком «де горить навесні», «де влітку».
    # Словник "код -> (назва кнопки, список місяців)"; None — "свої місяці", тоді показуємо звичайний вибір.
    # У радіо — коди (format_func показує назву), щоб вибір не скидався при зміні мови.
    SEASONS = {"all": (T("Увесь рік", "All year"), list(range(1, 13))),
               "spring": (T("Весна (бер–тра)", "Spring (Mar–May)"), [3, 4, 5]),
               "summer": (T("Літо (чер–сер)", "Summer (Jun–Aug)"), [6, 7, 8]),
               "autumn": (T("Осінь (вер–лис)", "Autumn (Sep–Nov)"), [9, 10, 11]),
               "custom": (T("Свої місяці…", "Custom months…"), None)}
    season = st.radio(T("Сезон", "Season"), list(SEASONS), horizontal=True, label_visibility="collapsed",
                      key=tab_key("map_season"), format_func=lambda s: SEASONS[s][0])
    months = SEASONS[season][1]
    if months is None:
        # format_func показує "Січ" замість 1
        months = st.multiselect(T("Місяці", "Months"), list(range(1, 13)), default=[12, 1, 2], key=tab_key("map_months"),
                                format_func=lambda m: MONTHS[m - 1])
    # Скільки днів горіла кожна клітинка у вибрані місяці (деталі — у firecal/mapgrid.py).
    # Порожньо, якщо в зоні немає пожеж АБО не вибрано жодного місяця — тоді карту не малюємо
    # (раніше без місяців карта падала з ValueError). НЕ st.stop(): він обірвав би й наступні вкладки.
    agg = mapgrid.burn_days(cells, months) if not cells.empty else pd.DataFrame()
    if agg.empty:
        st.info(T("Немає пожеж у вибраній зоні/періоді.", "No fires in the selected area/period.") if months
                else T("Виберіть хоча б один місяць.", "Select at least one month."))
    else:
        # Плавна картинка «де горить найчастіше», розмита в градусах, а не в пікселях (див. mapgrid.py).
        # НЕ density_map: та додає плями, що накладаються, і при віддаленні заливає червоним усю країну.
        smooth = mapgrid.smooth_image(agg, cell_deg)
        # Друга, різкіша картинка для великого наближення: розмиття мале, тож клітинки видно як клітинки,
        # а не як пляму розміром з місто. cap беремо від першої, щоб кольори збігались.
        sharp = mapgrid.smooth_image(agg, cell_deg, upsample=6, sigma_cells=0.35, cap=smooth["cap"])
        ZOOM_SWITCH = 8  # з цього зуму показуємо різку картинку (7.5-9: підбери на око)
        # choropleth_map — квадрати клітинок. Їх робимо НЕВИДИМИМИ (marker_opacity=0 нижче): вони потрібні
        # лише для підказки при наведенні (точне число клітинки) і для шкали кольорів праворуч.
        fig = px.choropleth_map(
            agg, geojson=mapgrid.cells_geojson(agg, cell_deg), locations="id", color="днів горіння",
            range_color=(0, smooth["cap"]),  # та сама шкала, що й у картинки
            # FADE_SCALE: тихі місця прозорі, видно лише гарячі (пояснення — у mapgrid.py)
            color_continuous_scale=mapgrid.FADE_SCALE,
            center=dict(lat=(map_box[1] + map_box[3]) / 2, lon=(map_box[0] + map_box[2]) / 2),  # центр зони
            # чим ширша зона (у градусах довготи), тим дальше "відсуваємо" карту
            zoom=5 if map_box[2] - map_box[0] > 9 else 6 if map_box[2] - map_box[0] > 4 else 7,
            map_style="carto-positron",
        )
        # marker_line_width=0 — без контурів квадратів (інакше сітка ліній забиває кольори);
        # hovertemplate — підказка: дні горіння і центр клітинки замість технічного id
        fig.update_traces(marker_line_width=0, marker_opacity=0, customdata=agg[["lat", "lon"]],
                          hovertemplate=T("%{z} днів горіння<br>%{customdata[0]:.2f}° пн. ш., "
                                          "%{customdata[1]:.2f}° сх. д.<extra></extra>",
                                          "%{z} fire days<br>%{customdata[0]:.2f}° N, "
                                          "%{customdata[1]:.2f}° E<extra></extra>"))
        # map.layers — додаткові шари карти: наша картинка, натягнута на свої 4 кути.
        # below="traces" — під невидимими квадратами, щоб наведення мишею й далі їх «ловило».
        fig.update_layout(height=640, margin=dict(l=0, r=0, t=0, b=70),
                          # Без "(згладжено)": з зуму ZOOM_SWITCH видно різку картинку, а шкала в обох та сама (спільний cap)
                          # з роками: інакше число «219 днів» висить у повітрі — за який період?
                          coloraxis_colorbar=dict(
                              title=dict(text=T(f"Днів горіння за {years_label}", f"Fire days in {years_label}"),
                                         side="top"),
                              # Горизонтально під картою (як у календаря): вертикальна праворуч на телефоні
                              # забирала пів ширини. yref="container", y=0 — у нижньому відступі margin.b
                              orientation="h", yref="container", y=0, yanchor="bottom", thickness=12, len=0.8,
                              ticklabeloverflow="allow"),
                          map_layers=[
                              # Згладжена: видима до зуму ZOOM_SWITCH (maxzoom: на ньому й вище шар ховається)
                              dict(sourcetype="image", source=smooth["source"],
                                   coordinates=smooth["coordinates"], below="traces", maxzoom=ZOOM_SWITCH),
                              # Різка: видима від зуму ZOOM_SWITCH (minzoom: нижче шар схований)
                              dict(sourcetype="image", source=sharp["source"],
                                   coordinates=sharp["coordinates"], below="traces", minzoom=ZOOM_SWITCH)])
        st.plotly_chart(fig, width="stretch")

# =====================================================================
# 5. MODIS vs VIIRS — доказ для журі, НАВІЩО потрібна гармонізація
# =====================================================================
with tab_cmp:
    how_to_read(T("Навіщо узгоджувати два супутники.", "Why the two satellites must be harmonized."),
                T("Гортайте згори вниз: проблема → рішення → поправка.",
                  "Scroll from top to bottom: problem → solution → correction."),
                T("MODIS (з 2000, піксель ~1 км) і VIIRS (з 2012, піксель 375 м) бачать ту саму пожежу по-різному: "
                  "VIIRS дає набагато більше точок. Ми зводимо обидва до спільної сітки (осередки горіння), "
                  "об'єднуємо, а роки до 2012 множимо на поправку k для кожного місяця. Деталі — «Методика».",
                  "MODIS (since 2000, ~1 km pixel) and VIIRS (since 2012, 375 m pixel) see the same fire differently: "
                  "VIIRS gives many more points. We map both onto a common grid (fire cells), merge them, "
                  "and multiply the years before 2012 by a monthly correction k. Details — “Methodology”."))
    # Рік старту VIIRS — для вертикальної позначки на графіках (None, якщо VIIRS у даних немає)
    v_year = viirs_start.year if viirs_start is not None else None

    def year_lines(df: pd.DataFrame, lines: list[tuple[str, str, str, float]], title: str, unit: str):
        """Лінії «по роках»: lines = [(колонка, назва в легенді, колір, товщина)] + позначка «запуск VIIRS».

        x — роки рядками ("2012"): з type="category" plotly не малює дробових років на осі.
        """
        x = [str(y) for y in df.index]
        fig = go.Figure()
        for col, name, color, width in lines:
            if col in df:
                fig.add_trace(go.Scatter(x=x, y=df[col], name=name, mode="lines+markers",
                                         line=dict(color=color, width=width), marker=dict(size=5),
                                         hovertemplate=f"{name}, %{{x}} — {unit}: <b>%{{y:,.0f}}</b><extra></extra>"))
        if v_year is not None and str(v_year) in x:
            vlabel = T("запуск VIIRS", "VIIRS launch")
            # Вертикальна пунктирна лінія у рік запуску VIIRS: yref="paper" — від низу до верху графіка.
            # Пастка: на осі-категорії рядок "2012" plotly читає як НОМЕР позиції 2012 (лінія відлітає
            # далеко праворуч і стискає графік). Тому даємо саме номер позиції року в списку x.
            pos = x.index(str(v_year))
            fig.add_shape(type="line", x0=pos, x1=pos, y0=0, y1=1, yref="paper",
                          line=dict(color=C_NORM, dash="dot"))
            fig.add_annotation(x=pos, y=1, yref="paper", text=vlabel, showarrow=False,
                               yanchor="bottom", font=dict(color=C_NORM))
        fig.update_layout(title=title, yaxis_title=unit, xaxis_type="category", separators=SEP, height=340,
                          margin=dict(l=0, r=0, t=50, b=40), legend=dict(orientation="h", yref="container", y=0, yanchor="bottom"))
        return fig

    # --- 1. ПРОБЛЕМА: сирі точки. groupby(рік, датчик).size() — скільки точок; unstack — датчики в колонки
    st.subheader(T("1. Проблема: VIIRS бачить набагато більше точок", "1. Problem: VIIRS sees many more points"))
    # Пораховано в run_pipeline (pts_sum): точки за роками і по днях для кожного датчика.
    # По днях — щоб порахувати, у скільки разів VIIRS бачить більше, коли працюють обидва
    raw, pts_daily = pts_sum["by_year"], pts_sum["by_day"]
    r_pts = harmonize.overlap_ratio(pts_daily.get("MODIS", pd.Series(dtype=float)),
                                    pts_daily.get("VIIRS", pd.Series(dtype=float)), viirs_start)
    if r_pts:
        st.markdown(T(f"З {v_year} року VIIRS дає у **{words.times_words(r_pts)} більше точок**, ніж MODIS, на ті самі "
                      f"пожежі. Якщо просто скласти точки, {v_year} виглядає як різкий ріст пожеж, хоча змінився "
                      "лише супутник.",
                      f"Since {v_year} VIIRS gives **{words.times_words(r_pts, True)} more points** than MODIS for the "
                      f"same fires. If you simply add up the points, {v_year} looks like a sharp rise in fires, although "
                      "only the satellite changed."))
    st.plotly_chart(year_lines(raw, [("MODIS", "MODIS", C_MODIS, 2), ("VIIRS", "VIIRS", C_VIIRS, 2)],
                               T("Сирі точки супутників за рік", "Raw satellite points per year"),
                               T("точок", "points")), width="stretch")

    # --- 2. РІШЕННЯ: спільна сітка + об'єднання. Сума осередків за рік для кожного ряду
    st.subheader(T("2. Рішення: спільна сітка ~10 км і обидва супутники разом",
                   "2. Solution: a common ~10 km grid and both satellites together"))
    yr = daily.groupby(daily.index.year)[["modis", "viirs"]].sum()
    yr["harm"] = analytics.yearly_totals(activity)
    st.markdown(T("Точки обох супутників збираємо в ділянки ~10 км: ділянка, де за день був вогонь, — один "
                  "**осередок горіння**, скільки б точок туди не впало. Після 2012 беремо все, що бачив хоч один "
                  "супутник; до 2012 — MODIS з поправкою (крок 3). Помаранчева лінія — один безперервний ряд: "
                  "роки до і після запуску VIIRS тепер можна чесно порівнювати.",
                  "Points of both satellites are grouped into ~10 km cells: a cell with fire on a day is one "
                  "**fire cell**, no matter how many points fell into it. After 2012 we take everything seen by at "
                  "least one satellite; before 2012 — MODIS with a correction (step 3). The orange line is one "
                  "continuous series: years before and after the VIIRS launch can now be compared fairly."))
    st.plotly_chart(year_lines(yr, [("modis", T("Лише MODIS", "MODIS only"), C_MODIS, 1.5),
                                    ("viirs", T("Лише VIIRS", "VIIRS only"), C_VIIRS, 1.5),
                                    ("harm", T("Обидва супутники, узгоджено", "Both satellites, harmonized"), C_HARM, 4)],
                               T("Осередки горіння за рік", "Fire cells per year"), T("осередків", "fire cells")),
                    width="stretch")

    # --- 3. ПОПРАВКА: k по місяцях стовпчиками — видно, що взимку і влітку вона різна
    st.subheader(T("3. Поправка для років до 2012", "3. Correction for the years before 2012"))
    if viirs_start is None:
        st.info(T("VIIRS у даних немає — поправку рахувати нема з чим, показуємо лише MODIS.",
                  "There is no VIIRS in the data — nothing to calibrate against, showing MODIS only."))
    else:
        st.markdown(T(f"У роки, коли працюють обидва супутники (з {viirs_start:%d.%m.%Y}), рахуємо, у скільки разів "
                      "MODIS недобачає в кожному місяці. Роки до 2012 множимо на це число — і вони стають "
                      "порівнянними з сучасними.",
                      f"In the years when both satellites operate (since {viirs_start:%d.%m.%Y}) we compute how many "
                      "times MODIS under-detects in each month. Years before 2012 are multiplied by this number — "
                      "and become comparable with recent ones."))
        fig = go.Figure(go.Bar(x=MONTHS, y=k.values, marker_color=C_HARM,
                               text=[words.fmt_times(v, EN) for v in k.values], textposition="outside",
                               hovertemplate="%{x}: MODIS × %{y:.2f}<extra></extra>"))
        fig.add_hline(y=1, line_dash="dot", line_color=C_NORM,
                      annotation_text=T("×1 — без поправки", "×1 — no correction"))
        fig.update_layout(title=dict(text=T("Поправка для MODIS по місяцях", "MODIS correction by month"),
                                     subtitle_text=T("науково: k = union / MODIS", "k = union / MODIS")),
                          separators=SEP,
                          yaxis_title=T("у скільки разів", "times"), height=340, margin=dict(l=0, r=0, t=70, b=0),
                          yaxis_range=[0, float(k.max()) * 1.2])
        st.plotly_chart(fig, width="stretch")

# =====================================================================
# 6. МЕТОДИКА — текст для журі (Markdown)
# =====================================================================
# Тексти методики — дві мови. Умова хакатону англійською, журі може бути міжнародним.
# УВАГА: міняєш методику — онови ОБИДВА тексти.
METHOD_UA = """
#### Словничок: у додатку → науково
| На екрані | Науковий термін |
|---|---|
| осередок горіння | пожежо-клітинко-день: клітинка сітки, де за добу був хоч один хот-спот |
| зазвичай | медіана по роках |
| незвично гарячий тиждень | робастний z ≥ 2 (медіана/MAD) |
| у 9 з 10 років нижче | 90-й перцентиль |
| поправка для років до 2012 | коефіцієнт калібрування k = union / MODIS |
| достовірність низька / середня / висока | confidence low / nominal / high |
| обидва супутники, узгоджено | гармонізований ряд |

### Як ми гармонізуємо MODIS і VIIRS
1. **Єдина схема.** Обидва формати FIRMS зводимо до спільних полів; достовірність MODIS (0–100) і VIIRS (l/n/h)
   — до трьох класів *low / nominal / high* (MODIS: <30 low, ≥80 high). Лишаємо лише рослинні пожежі (`type = 0`).
2. **Спільна сітка.** Точки «прилипають» до клітинок (за замовч. 0.1° ≈ 8×11 км). Одиниця — **пожежо-клітинко-день**:
   клітинка, де за добу був хоч один хот-спот. Це прибирає різницю в розмірі пікселя (1 км vs 375 м).
3. **Об'єднання.** Клітинко-день активний, якщо його побачив MODIS **або** VIIRS.
4. **Калібрування.** У роки, де працюють обидва датчики, рахуємо k = union / MODIS для кожного місяця.
   Для років лише з MODIS (до 2012) ряд = MODIS × k. Так виходить безперервний ряд з 2000 р.
   До 04.07.2002 літав лише один MODIS (Terra, без Aqua), тож ці дні ще множимо на r = (Terra+Aqua) / Terra
   для кожного місяця (рахуємо у 2002–2011, ≈1,8).
   k завжди рахуємо на всіх роках вибраної зони, тож слайдер років не змінює минулих значень.
5. **Календар.** Рік × тиждень; **норма** = медіана по роках; **аномалія** = робастний z ≥ 2;
   **критичні періоди** = 25% тижнів із найбільшою середньою активністю.
   Тижні рахуються від 1 січня (52 на рік, останні 1–2 дні — у 52-му), щоб усі роки мали однакову сітку.
6. **Раннє попередження.** Активність за останні N днів порівнюємо з тими самими днями календаря
   в інші роки: вище 75% років — 🟡 підвищений, вище 90% — 🟠 високий, робастний z ≥ 2 — 🔴 дуже високий.
   Тенденція — як горіння зазвичай змінювалось у наступні 4 тижні в минулі роки.

Джерело даних: NASA FIRMS (MODIS C6.1, VIIRS S-NPP 375 м).
"""

METHOD_EN = """
#### Glossary: in the app → scientific term
| On screen | Scientific term |
|---|---|
| fire cell | fire-cell-day: a grid cell with at least one hotspot during the day |
| usual | median across years |
| unusually hot week | robust z ≥ 2 (median / MAD) |
| lower in 9 of 10 years | 90th percentile |
| correction for the years before 2012 | calibration factor k = union / MODIS |
| point confidence low / nominal / high | FIRMS confidence low / nominal / high |
| both satellites, harmonized | harmonized series |

### How we harmonize MODIS and VIIRS
1. **Common schema.** Both FIRMS formats are mapped to shared fields; MODIS confidence (0–100) and VIIRS
   confidence (l/n/h) become three classes *low / nominal / high* (MODIS: <30 low, ≥80 high).
   Only vegetation fires are kept (`type = 0`).
2. **Common grid.** Points snap to grid cells (0.1° ≈ 8×11 km by default). The unit is the **fire-cell-day**:
   a cell with at least one hotspot during the day. This removes the pixel-size difference (1 km vs 375 m).
3. **Union.** A cell-day is active if MODIS **or** VIIRS detected fire there.
4. **Calibration.** In years when both sensors operate we compute k = union / MODIS for each calendar month.
   For MODIS-only years (before 2012) the series is MODIS × k. This gives one continuous series from 2000.
   Before 04.07.2002 only one MODIS flew (Terra, no Aqua yet), so those days are also multiplied by
   r = (Terra+Aqua) / Terra per month (computed on 2002–2011, ≈1.8).
   k is always computed on all years of the selected zone, so the year slider never changes past values.
5. **Calendar.** Year × week; **normal** = median across years; **anomaly** = robust z ≥ 2;
   **critical periods** = the 25% of weeks with the highest mean activity.
   Weeks are counted from 1 January (52 per year, the last 1–2 days go to week 52), so all years share one grid.
6. **Early warning.** Activity over the last N days is compared with the same calendar days in other years:
   above 75% of years — 🟡 elevated, above 90% — 🟠 high, robust z ≥ 2 — 🔴 very high.
   The outlook shows how burning usually changed over the following 4 weeks in past years.

Data source: NASA FIRMS (MODIS C6.1, VIIRS S-NPP 375 m). Coverage: Ukraine.
"""

with tab_about:
    # Мова — та сама, що в перемикачі бічної панелі (окремий перемикач тут більше не потрібен)
    st.markdown(METHOD_EN if EN else METHOD_UA)
