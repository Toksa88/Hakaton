"""
Крок 2. Гармонізація MODIS і VIIRS — серце проєкту.

Чому сирі точки не можна порівнювати:
  * піксель MODIS ~1 км, піксель VIIRS ~375 м -> одна й та сама пожежа дає
    в VIIRS у кілька разів БІЛЬШЕ точок;
  * VIIRS бачить дрібніші вогнища, яких MODIS не помічає;
  * MODIS працює з 2000 р., VIIRS (S-NPP) — лише з 2012 р.
  Тобто "у 2020 точок у 5 разів більше, ніж у 2005" — це артефакт датчика, а не пожеж.

Наш підхід (3 кроки, легко пояснити журі):
  1. Однакова сітка. Кожну точку "прилипаємо" до клітинки сітки (напр. 0.1°).
     Одиниця виміру = "пожежо-клітинко-день": клітинка, в якій за добу був хоч
     один хот-спот. Так 10 точок VIIRS і 1 точка MODIS від одного поля = 1 подія.
  2. Об'єднання. Для кожної клітинко-дня знаємо, хто її бачив: MODIS, VIIRS чи обидва.
     Union (MODIS ∪ VIIRS) = найповніша картина горіння.
  3. Калібрування. У період, коли працюють ОБИДВА датчики (2012+), рахуємо,
     у скільки разів union більший за MODIS-only (окремо для кожного місяця, бо
     сезонність різна). Цим коефіцієнтом "дотягуємо" роки до 2012, де є лише MODIS.
     Результат — один безперервний ряд з 2000 р. у "VIIRS-еквівалентних" одиницях.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CONF_ORDER = {"low": 0, "nominal": 1, "high": 2}


def filter_points(
    df: pd.DataFrame,
    bbox: tuple[float, float, float, float] | None = None,  # (lon_min, lat_min, lon_max, lat_max)
    start=None,
    end=None,
    min_conf: str = "nominal",
    vegetation_only: bool = True,
) -> pd.DataFrame:
    """Відбір точок: зона інтересу, період, мінімальна достовірність, лише рослинні пожежі."""
    m = pd.Series(True, index=df.index)
    if bbox is not None:
        lon0, lat0, lon1, lat1 = bbox
        m &= df["lon"].between(lon0, lon1) & df["lat"].between(lat0, lat1)
    if start is not None:
        m &= df["date"] >= pd.Timestamp(start)
    if end is not None:
        m &= df["date"] <= pd.Timestamp(end)
    m &= df["conf_class"].map(CONF_ORDER).fillna(1) >= CONF_ORDER[min_conf]
    if vegetation_only:
        m &= df["type"] == 0  # відкидаємо заводи, вулкани, офшорні факели
    return df[m]


def to_fire_cell_days(df: pd.DataFrame, cell_deg: float = 0.1) -> pd.DataFrame:
    """Точки -> пожежо-клітинко-дні з прапорцями, які датчики їх бачили."""
    if df.empty:
        return pd.DataFrame(columns=["date", "cx", "cy", "lat", "lon", "modis", "viirs", "frp"])
    d = df.assign(
        cx=np.floor(df["lon"] / cell_deg).astype(int),
        cy=np.floor(df["lat"] / cell_deg).astype(int),
        is_modis=df["sensor"].eq("MODIS"),
        is_viirs=df["sensor"].eq("VIIRS"),
    )
    g = d.groupby(["date", "cx", "cy"], as_index=False).agg(
        modis=("is_modis", "any"),
        viirs=("is_viirs", "any"),
        frp=("frp", "sum"),
    )
    # центр клітинки — для карти
    g["lon"] = (g["cx"] + 0.5) * cell_deg
    g["lat"] = (g["cy"] + 0.5) * cell_deg
    return g


def daily_counts(cells: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    """Кількість пожежо-клітинок за кожну добу (включно з днями без пожеж = 0)."""
    if cells.empty:
        idx = pd.date_range(start or "2020-01-01", end or "2020-12-31", freq="D")
        return pd.DataFrame(0, index=idx, columns=["modis", "viirs", "both", "union", "frp"])
    c = cells.assign(
        both=cells["modis"] & cells["viirs"],
        union=True,
    )
    daily = c.groupby("date").agg(
        modis=("modis", "sum"), viirs=("viirs", "sum"),
        both=("both", "sum"), union=("union", "sum"), frp=("frp", "sum"),
    )
    idx = pd.date_range(start or daily.index.min(), end or daily.index.max(), freq="D")
    return daily.reindex(idx, fill_value=0).astype(float)


def calibration_factors(daily: pd.DataFrame, viirs_start: pd.Timestamp | None) -> pd.Series:
    """
    Коефіцієнт k[місяць] = union / modis у період перекриття (обидва датчики працюють).
    Повертає Series з індексом 1..12. Якщо даних мало — береться загальний k.
    """
    if viirs_start is None:
        return pd.Series(1.0, index=range(1, 13))
    ov = daily[daily.index >= viirs_start]
    by_m = ov.groupby(ov.index.month)[["union", "modis"]].sum()
    k_all = ov["union"].sum() / ov["modis"].sum() if ov["modis"].sum() > 0 else 1.0
    k = (by_m["union"] / by_m["modis"].replace(0, np.nan))
    # місяці з дуже малою кількістю MODIS-подій дають шумний k -> замінюємо загальним
    k[by_m["modis"] < 20] = np.nan
    return k.reindex(range(1, 13)).fillna(k_all).clip(lower=1.0)


def harmonized_series(daily: pd.DataFrame, viirs_start: pd.Timestamp | None) -> tuple[pd.Series, pd.Series]:
    """
    Єдиний ряд "активності горіння":
      * де є VIIRS -> union (MODIS ∪ VIIRS)
      * до появи VIIRS -> MODIS × k[місяць]
    Повертає (ряд, коефіцієнти k).
    """
    k = calibration_factors(daily, viirs_start)
    est = daily["modis"] * daily.index.month.map(k).to_numpy()
    if viirs_start is None:
        return est.rename("activity"), k
    act = daily["union"].where(daily.index >= viirs_start, est)
    return act.rename("activity"), k
