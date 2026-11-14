"""
Крок 1. Завантаження сирих CSV з NASA FIRMS і приведення до ОДНІЄЇ схеми.

Проблема: MODIS і VIIRS мають різні колонки і різну "мову" достовірності:
  * MODIS:  brightness, bright_t31, confidence = число 0..100
  * VIIRS:  bright_ti4, bright_ti5, confidence = 'l' / 'n' / 'h'  (або low/nominal/high)

Тут ми перетворюємо обидва формати на спільну таблицю:
  lat, lon, date, time, sensor, satellite, frp, conf_class, daynight, type

Шлях даних у цьому файлі:
  CSV-файли у data/raw/  --pd.read_csv-->  сира таблиця FIRMS
      --normalize()-->  таблиця в єдиній схемі (одна на файл)
      --pd.concat-->    одна велика таблиця з усіх файлів
      --load_cached()-> збережена копія в data/cache/*.parquet, щоб наступного разу читати швидко
"""
# Дозволяє писати підказки типів на кшталт `Path | None` навіть у старших версіях Python.
from __future__ import annotations

import hashlib  # md5 — робимо короткий "відбиток" (хеш) зі списку файлів для імені кешу
from pathlib import Path  # Path — зручна робота зі шляхами: Path("a") / "b" / "c.csv"

import pandas as pd

# __file__ = шлях до цього файлу (firecal/load.py).
# .resolve() робить шлях повним, .parent — "на папку вище":
#   firecal/load.py -> firecal/ -> корінь проєкту. Далі дописуємо data/raw.
# Так шлях працює однаково, з якої б папки ви не запустили програму.
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"

# Порядок і назви колонок нашої єдиної схеми. Будь-яка таблиця після normalize() має саме їх.
#   lat, lon    — широта і довгота точки (градуси)
#   date        — дата знімку (тип datetime, щоб можна було брати рік/місяць/тиждень)
#   time        — час знімку UTC у форматі HHMM числом (напр. 1035 = 10:35)
#   sensor      — "MODIS" або "VIIRS"
#   satellite   — який саме супутник (Terra/Aqua для MODIS, N/1 для VIIRS)
#   frp         — Fire Radiative Power, потужність випромінювання пожежі (МВт)
#   conf_class  — достовірність, зведена до трьох класів: low / nominal / high
#   daynight    — "D" день або "N" ніч
#   type        — тип джерела (0 = рослинна пожежа; див. коментар у normalize)
UNIFIED_COLUMNS = [
    "lat", "lon", "date", "time", "sensor", "satellite",
    "frp", "conf_class", "daynight", "type",
]


def modis_conf_class(conf: pd.Series) -> pd.Series:
    """MODIS: число 0..100 -> клас. Пороги — стандартні для FIRMS (MODIS C6.1)."""
    # pd.to_numeric перетворює текст на числа. errors="coerce" = якщо щось не число
    # (порожньо, сміття) — ставимо NaN ("немає значення") замість того, щоб падати з помилкою.
    c = pd.to_numeric(conf, errors="coerce")
    # Створюємо стовпчик тієї ж довжини, де спершу ВСЮДИ "nominal".
    # index=conf.index — щоб рядки відповідали рядкам вхідного стовпчика.
    out = pd.Series("nominal", index=conf.index)
    # out[умова] = значення — замінюємо лише ті рядки, де умова True.
    # c < 30 дає стовпчик True/False; NaN порівнюється як False, тож такі рядки лишаються "nominal".
    out[c < 30] = "low"
    out[c >= 80] = "high"
    return out


def viirs_conf_class(conf: pd.Series) -> pd.Series:
    """VIIRS: 'l'/'n'/'h' або 'low'/'nominal'/'high' -> клас."""
    # .str — доступ до текстових операцій для КОЖНОГО елемента стовпчика:
    #   astype(str)  — на всяк випадок робимо все текстом
    #   str.strip()  — прибираємо пробіли по краях (" h " -> "h")
    #   str.lower()  — у нижній регістр ("High" -> "high")
    #   str[0]       — беремо першу літеру ("high" -> "h", "h" -> "h")
    # Так і короткий, і довгий запис зводяться до однієї літери.
    first = conf.astype(str).str.strip().str.lower().str[0]
    # map(словник) — замінює кожне значення за словником. Невідомі літери дадуть NaN,
    # і fillna("nominal") поставить їм середній клас.
    return first.map({"l": "low", "n": "nominal", "h": "high"}).fillna("nominal")


def detect_sensor(df: pd.DataFrame) -> str:
    """Визначаємо датчик за колонками (назви файлів можуть бути будь-які)."""
    cols = set(df.columns)  # множина назв колонок — швидка перевірка "чи є така колонка"
    # Колонка яскравості в каналі I4 буває лише у VIIRS
    if "bright_ti4" in cols:
        return "VIIRS"
    # Колонка "brightness" — лише у MODIS
    if "brightness" in cols:
        return "MODIS"
    # Запасний варіант: деякі вивантаження FIRMS мають колонку instrument з назвою датчика.
    # iloc[0] — значення з першого рядка.
    if "instrument" in cols:
        return str(df["instrument"].iloc[0]).upper()
    # Нічого не підійшло — краще чесно впасти з зрозумілим повідомленням, ніж мовчки рахувати не те.
    raise ValueError(f"Не можу визначити датчик за колонками: {sorted(cols)}")


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Сирий FIRMS DataFrame (MODIS або VIIRS) -> уніфікована схема."""
    # rename(columns=str.lower) — усі назви колонок у нижній регістр ("LATITUDE" -> "latitude"),
    # щоб далі не думати, як саме їх написали у файлі.
    df = df.rename(columns=str.lower)
    sensor = detect_sensor(df)
    # Достовірність у двох датчиків записана по-різному — викликаємо відповідну функцію.
    # Запис "A if умова else B" — це короткий if/else в один рядок.
    conf = modis_conf_class(df["confidence"]) if sensor == "MODIS" else viirs_conf_class(df["confidence"])

    # Будуємо нову таблицю зі словника "назва колонки -> значення".
    # df.get("колонка", запасне) — як df["колонка"], але якщо колонки немає, повертає запасне значення
    # (тут — стовпчик із заповнювачем), а не падає з KeyError. Так ми переживемо "урізані" CSV.
    out = pd.DataFrame({
        "lat": df["latitude"].astype(float),
        "lon": df["longitude"].astype(float),
        # pd.to_datetime: текст "2020-04-01" -> справжня дата, з якою можна рахувати
        "date": pd.to_datetime(df["acq_date"]),
        "time": df.get("acq_time", pd.Series(0, index=df.index)).astype(int),
        # Одне значення (рядок) pandas сам "розмножить" на всі рядки таблиці
        "sensor": sensor,
        "satellite": df.get("satellite", pd.Series("?", index=df.index)).astype(str),
        "frp": pd.to_numeric(df.get("frp"), errors="coerce"),
        "conf_class": conf,
        "daynight": df.get("daynight", pd.Series("D", index=df.index)).astype(str),
        # type: 0 = рослинна пожежа, 1 = вулкан, 2 = інше статичне джерело (заводи), 3 = офшор
        # fillna(0) — якщо типу немає, вважаємо рослинною пожежею; astype(int) — ціле число
        "type": pd.to_numeric(df.get("type", 0), errors="coerce").fillna(0).astype(int),
    })
    # out[список] — лишаємо лише ці колонки і саме в такому порядку
    return out[UNIFIED_COLUMNS]


def load_raw(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Читає всі *.csv з data/raw, нормалізує і склеює в одну таблицю."""
    # glob("*.csv") — знайти всі файли з розширенням .csv у папці; sorted — у стабільному порядку
    files = sorted(Path(raw_dir).glob("*.csv"))
    if not files:
        # Немає файлів — повертаємо ПОРОЖНЮ таблицю з правильними колонками,
        # щоб решта коду не падала (app.py сам покаже підказку "немає даних").
        return pd.DataFrame(columns=UNIFIED_COLUMNS)
    # "List comprehension": для кожного файлу f читаємо CSV і нормалізуємо — отримуємо список таблиць
    return combine([normalize(pd.read_csv(f)) for f in files])


def combine(parts: list[pd.DataFrame]) -> pd.DataFrame:
    """Склеює нормалізовані таблиці в одну і прибирає дублікати точок.

    Спільна для data/raw і для файлів, завантажених через сайт (app.get_points): раніше
    завантажені вручну файли не чистились, і той самий файл двічі подвоював кількість точок.
    """
    # concat склеює таблиці одна під одною. ignore_index=True — нумерація рядків наново 0,1,2,...
    df = pd.concat(parts, ignore_index=True)
    # одні й ті самі дані могли скачати двічі (архів + NRT) -> прибираємо точні дублікати
    # subset — рядки вважаються дублікатами, якщо збігаються саме ці колонки;
    # ignore_index=True — після викидання рядків знову нумеруємо 0,1,2,...
    return df.drop_duplicates(subset=["lat", "lon", "date", "time", "satellite"], ignore_index=True)


def load_cached(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Як load_raw, але кешує в parquet (CSV на десятки МБ читаються повільно).

    Parquet — двійковий формат таблиць: читається в рази швидше за CSV і зберігає типи
    (дати лишаються датами). Для нього потрібен пакет pyarrow (є в requirements.txt).
    """
    files = sorted(Path(raw_dir).glob("*.csv"))
    # Створюємо папку кешу, якщо її ще немає. parents=True — разом з батьківськими папками,
    # exist_ok=True — не падати, якщо папка вже існує.
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # ключ кешу залежить від списку файлів і їх розміру — додали файл -> кеш перебудується
    # sig ("підпис") виглядає так: "modis_2020.csv:123456|viirs_2020.csv:987654"
    sig = "|".join(f"{f.name}:{f.stat().st_size}" for f in files)
    # md5 перетворює довгий підпис на короткий набір символів; беремо перші 12 — для імені файлу досить
    key = hashlib.md5(sig.encode()).hexdigest()[:12]
    cache = CACHE_DIR / f"unified_{key}.parquet"
    if cache.exists():
        # Кеш для цього набору файлів уже є — просто читаємо його (швидко)
        return pd.read_parquet(cache)
    # Кешу немає — читаємо CSV (повільно) і зберігаємо результат на наступний раз.
    # Увага: старі unified_*.parquet самі не видаляються — папку data/cache/ можна чистити вручну.
    df = load_raw(raw_dir)
    if not df.empty:
        df.to_parquet(cache, index=False)
    return df
