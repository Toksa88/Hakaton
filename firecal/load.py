"""
Крок 1. Завантаження сирих CSV з NASA FIRMS і приведення до ОДНІЄЇ схеми.

Проблема: MODIS і VIIRS мають різні колонки і різну "мову" достовірності:
  * MODIS:  brightness, bright_t31, confidence = число 0..100
  * VIIRS:  bright_ti4, bright_ti5, confidence = 'l' / 'n' / 'h'  (або low/nominal/high)

Тут ми перетворюємо обидва формати на спільну таблицю:
  lat, lon, date, time, sensor, satellite, frp, conf_class, daynight, type
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"

UNIFIED_COLUMNS = [
    "lat", "lon", "date", "time", "sensor", "satellite",
    "frp", "conf_class", "daynight", "type",
]


def modis_conf_class(conf: pd.Series) -> pd.Series:
    """MODIS: число 0..100 -> клас. Пороги — стандартні для FIRMS (MODIS C6.1)."""
    c = pd.to_numeric(conf, errors="coerce")
    out = pd.Series("nominal", index=conf.index)
    out[c < 30] = "low"
    out[c >= 80] = "high"
    return out


def viirs_conf_class(conf: pd.Series) -> pd.Series:
    """VIIRS: 'l'/'n'/'h' або 'low'/'nominal'/'high' -> клас."""
    first = conf.astype(str).str.strip().str.lower().str[0]
    return first.map({"l": "low", "n": "nominal", "h": "high"}).fillna("nominal")


def detect_sensor(df: pd.DataFrame) -> str:
    """Визначаємо датчик за колонками (назви файлів можуть бути будь-які)."""
    cols = set(df.columns)
    if "bright_ti4" in cols:
        return "VIIRS"
    if "brightness" in cols:
        return "MODIS"
    if "instrument" in cols:
        return str(df["instrument"].iloc[0]).upper()
    raise ValueError(f"Не можу визначити датчик за колонками: {sorted(cols)}")


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Сирий FIRMS DataFrame (MODIS або VIIRS) -> уніфікована схема."""
    df = df.rename(columns=str.lower)
    sensor = detect_sensor(df)
    conf = modis_conf_class(df["confidence"]) if sensor == "MODIS" else viirs_conf_class(df["confidence"])

    out = pd.DataFrame({
        "lat": df["latitude"].astype(float),
        "lon": df["longitude"].astype(float),
        "date": pd.to_datetime(df["acq_date"]),
        "time": df.get("acq_time", pd.Series(0, index=df.index)).astype(int),
        "sensor": sensor,
        "satellite": df.get("satellite", pd.Series("?", index=df.index)).astype(str),
        "frp": pd.to_numeric(df.get("frp"), errors="coerce"),
        "conf_class": conf,
        "daynight": df.get("daynight", pd.Series("D", index=df.index)).astype(str),
        # type: 0 = рослинна пожежа, 1 = вулкан, 2 = інше статичне джерело (заводи), 3 = офшор
        "type": pd.to_numeric(df.get("type", 0), errors="coerce").fillna(0).astype(int),
    })
    return out[UNIFIED_COLUMNS]


def load_raw(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Читає всі *.csv з data/raw, нормалізує і склеює в одну таблицю."""
    files = sorted(Path(raw_dir).glob("*.csv"))
    if not files:
        return pd.DataFrame(columns=UNIFIED_COLUMNS)
    parts = [normalize(pd.read_csv(f)) for f in files]
    df = pd.concat(parts, ignore_index=True)
    # одні й ті самі дані могли скачати двічі (архів + NRT) -> прибираємо точні дублікати
    return df.drop_duplicates(subset=["lat", "lon", "date", "time", "satellite"])


def load_cached(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Як load_raw, але кешує в parquet (CSV на десятки МБ читаються повільно)."""
    files = sorted(Path(raw_dir).glob("*.csv"))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # ключ кешу залежить від списку файлів і їх розміру — додали файл -> кеш перебудується
    sig = "|".join(f"{f.name}:{f.stat().st_size}" for f in files)
    key = hashlib.md5(sig.encode()).hexdigest()[:12]
    cache = CACHE_DIR / f"unified_{key}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    df = load_raw(raw_dir)
    if not df.empty:
        df.to_parquet(cache, index=False)
    return df
