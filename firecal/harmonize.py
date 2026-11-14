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

from firecal import regions

# Класи достовірності як числа: так можна сказати "не нижче за nominal" через звичайне >=
CONF_ORDER = {"low": 0, "nominal": 1, "high": 2}


def filter_points(
    df: pd.DataFrame,
    bbox: tuple[float, float, float, float] | None = None,  # (lon_min, lat_min, lon_max, lat_max)
    start=None,
    end=None,
    min_conf: str = "nominal",
    vegetation_only: bool = True,
    oblasts: list[str] | None = None,
    exclude_war: bool = False,
) -> pd.DataFrame:
    """
    Відбір точок: зона інтересу, період, мінімальна достовірність, лише рослинні пожежі.
      oblasts     — лишити лише точки з цих областей (None = без фільтра за областями)
      exclude_war — викинути точки з прифронтових областей після 24.02.2022 (regions.FRONTLINE):
                    там багато хот-спотів від обстрілів, а не від с/г палів
    """
    # "Маска" — стовпчик True/False того ж розміру, що й таблиця. Спочатку всі True (беремо все),
    # далі кожна умова через &= ("і") вимикає непотрібні рядки. Наприкінці df[m] лишає лише True.
    m = pd.Series(True, index=df.index)
    if bbox is not None:
        lon0, lat0, lon1, lat1 = bbox
        m &= df["lon"].between(lon0, lon1) & df["lat"].between(lat0, lat1)  # точка всередині прямокутника
    if start is not None:
        m &= df["date"] >= pd.Timestamp(start)
    if end is not None:
        m &= df["date"] <= pd.Timestamp(end)
    # map перетворює "low"/"nominal"/"high" у 0/1/2, щоб можна було порівнювати "не нижче ніж"
    m &= df["conf_class"].map(CONF_ORDER).fillna(1) >= CONF_ORDER[min_conf]
    if vegetation_only:
        m &= df["type"] == 0  # відкидаємо заводи, вулкани, офшорні факели
    if oblasts is not None or exclude_war:
        # Колонку "oblast" app.py додає один раз при завантаженні; якщо її нема — рахуємо тут
        obl = df["oblast"] if "oblast" in df else pd.Series(regions.oblast_of(df["lat"], df["lon"]), index=df.index)
        if oblasts is not None:
            m &= obl.isin(oblasts)  # isin — "значення є в цьому списку"
        if exclude_war:
            # ~ — логічне "НЕ": викидаємо рядки, де ОДНОЧАСНО область прифронтова І дата після початку війни
            m &= ~(obl.isin(regions.FRONTLINE) & (df["date"] >= regions.WAR_START))
    return df[m]


def to_fire_cell_days(df: pd.DataFrame, cell_deg: float = 0.1) -> pd.DataFrame:
    """Точки -> пожежо-клітинко-дні з прапорцями, які датчики їх бачили."""
    # Після фільтрів точок може не лишитись — повертаємо порожню таблицю з потрібними колонками
    if df.empty:
        return pd.DataFrame(columns=["date", "cx", "cy", "lat", "lon", "modis", "viirs", "frp"])
    # assign додає нові колонки. cx, cy — номер клітинки сітки по довготі/широті.
    # floor(lon / 0.1) = "в якій смузі шириною 0.1° лежить точка": 30.37 -> 303, 30.39 -> 303 (та сама клітинка)
    # floor (округлення ВНИЗ), а не round: так межі клітинок рівні — 30.30..30.39(9) завжди клітинка 303.
    # is_modis / is_viirs — True/False для кожної точки: "чи це точка такого датчика"
    d = df.assign(
        cx=np.floor(df["lon"] / cell_deg).astype(int),
        cy=np.floor(df["lat"] / cell_deg).astype(int),
        is_modis=df["sensor"].eq("MODIS"),
        is_viirs=df["sensor"].eq("VIIRS"),
    )
    # groupby: збираємо всі точки з однаковими (день, клітинка) в одну групу = один пожежо-клітинко-день.
    # agg каже, що порахувати для групи: any = "чи була хоч одна точка MODIS/VIIRS", sum = сумарна потужність FRP.
    g = d.groupby(["date", "cx", "cy"], as_index=False).agg(
        modis=("is_modis", "any"),
        viirs=("is_viirs", "any"),
        frp=("frp", "sum"),
    )
    # Тепер g — по одному рядку на (день, клітинка): колонки date, cx, cy, modis, viirs, frp.
    # центр клітинки — для карти: клітинка 303 займає 30.3..30.4°, її центр = (303 + 0.5) * 0.1 = 30.35°
    g["lon"] = (g["cx"] + 0.5) * cell_deg
    g["lat"] = (g["cy"] + 0.5) * cell_deg
    return g


def daily_counts(cells: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    """Кількість пожежо-клітинок за кожну добу (включно з днями без пожеж = 0)."""
    # Пожеж немає взагалі — таблиця з нулями на весь період, щоб графіки не падали.
    # `start or "2020-01-01"` = беремо start, а якщо його не передали (None) — запасну дату.
    if cells.empty:
        idx = pd.date_range(start or "2020-01-01", end or "2020-12-31", freq="D")
        return pd.DataFrame(0, index=idx, columns=["modis", "viirs", "both", "union", "frp"])
    # Додаємо дві допоміжні колонки до кожної клітинко-дня:
    #   both  — True, якщо клітинку бачили ОБИДВА датчики (& = логічне "і" по рядках)
    #   union — завжди True: кожен рядок і є подією "MODIS або VIIRS" (так його легко порахувати сумою)
    c = cells.assign(
        both=cells["modis"] & cells["viirs"],
        union=True,
    )
    # Групуємо по дню і рахуємо: sum від True/False = кількість клітинок, де умова True
    daily = c.groupby("date").agg(
        modis=("modis", "sum"), viirs=("viirs", "sum"),
        both=("both", "sum"), union=("union", "sum"), frp=("frp", "sum"),
    )
    # Тепер daily — по рядку на кожен день, У ЯКОМУ БУЛИ пожежі; індекс (ліва "колонка") = дата.
    # date_range будує повний список днів від start до end з кроком 1 день (freq="D").
    idx = pd.date_range(start or daily.index.min(), end or daily.index.max(), freq="D")
    # reindex додає дні, яких не було в даних (без пожеж), зі значенням 0 —
    # інакше календар мав би "дірки" і середні рахувались би неправильно
    return daily.reindex(idx, fill_value=0).astype(float)


def calibration_factors(daily: pd.DataFrame, viirs_start: pd.Timestamp | None) -> pd.Series:
    """
    Коефіцієнт k[місяць] = union / modis у період перекриття (обидва датчики працюють).
    Повертає Series з індексом 1..12. Якщо даних мало — береться загальний k.
    """
    # Приклад: у квітні 2012–2024 MODIS побачив 1000 клітинко-днів, а MODIS ∪ VIIRS — 2500.
    # Отже k[4] = 2.5: у квітні MODIS "недобачає" в 2.5 раза. Квітень 2005 року (лише MODIS)
    # множимо на 2.5 — і він стає порівнянним із квітнем 2020.
    #
    # Даних VIIRS немає зовсім — калібрувати нема з чим, k = 1 для всіх місяців (ряд = MODIS).
    if viirs_start is None:
        return pd.Series(1.0, index=range(1, 13))
    ov = daily[daily.index >= viirs_start]  # "overlap" — лише дні, коли вже працює VIIRS
    # Групуємо дні перекриття за номером місяця (1..12) і сумуємо union та modis всередині місяця
    by_m = ov.groupby(ov.index.month)[["union", "modis"]].sum()
    # k_all — один спільний коефіцієнт за весь період перекриття; запасний варіант для "бідних" місяців.
    # Якщо в daily немає жодного дня з VIIRS, ov порожній і k_all = 1 — MODIS не підсилюється.
    # Тому run_pipeline передає сюди ВСІ роки зони, а не лише вибрані слайдером.
    k_all = ov["union"].sum() / ov["modis"].sum() if ov["modis"].sum() > 0 else 1.0
    # replace(0, nan) — щоб не ділити на нуль (результат буде nan, і далі ми його замінимо)
    k = (by_m["union"] / by_m["modis"].replace(0, np.nan))
    # місяці з дуже малою кількістю MODIS-подій дають шумний k -> замінюємо загальним
    k[by_m["modis"] < 20] = np.nan
    # reindex(range(1, 13)) — гарантуємо всі 12 місяців (місяць без даних з'явиться як NaN);
    # fillna(k_all) — усі NaN (немає даних / мало подій / ділення на нуль) замінюємо загальним k;
    # clip(lower=1): k не може бути < 1, бо union завжди містить усе, що бачив MODIS
    return k.reindex(range(1, 13)).fillna(k_all).clip(lower=1.0)


def harmonized_series(daily: pd.DataFrame, viirs_start: pd.Timestamp | None) -> tuple[pd.Series, pd.Series]:
    """
    Єдиний ряд "активності горіння":
      * де є VIIRS -> union (MODIS ∪ VIIRS)
      * до появи VIIRS -> MODIS × k[місяць]
    Повертає (ряд, коефіцієнти k).
    """
    k = calibration_factors(daily, viirs_start)
    # Для кожного дня беремо k його місяця і множимо MODIS на нього — "оцінка" повної картини.
    # daily.index.month — номер місяця кожного дня; .map(k) — замінює номер на k цього місяця;
    # .to_numpy() — робимо звичайний масив, щоб множення йшло "рядок до рядка" без вирівнювання індексів.
    est = daily["modis"] * daily.index.month.map(k).to_numpy()
    # rename("activity") — просто дає ряду назву (вона з'явиться в підписах/таблицях)
    if viirs_start is None:
        return est.rename("activity"), k
    # where(умова, інакше): де умова True (день після старту VIIRS) — беремо union, інакше — оцінку est
    act = daily["union"].where(daily.index >= viirs_start, est)
    return act.rename("activity"), k


def run_pipeline(points: pd.DataFrame, bbox, start, end, min_conf: str = "nominal", cell_deg: float = 0.1,
                 oblasts: list[str] | None = None, exclude_war: bool = False):
    """
    Увесь конвеєр: точки -> відбір -> клітинки -> денні суми -> гармонізований ряд.

    ВАЖЛИВО (виправлений баг): коефіцієнти k рахуємо на ВСІХ роках даних цієї зони,
    а start/end (слайдер років) лише ОБРІЗАЮТЬ результат для показу.
    Раніше k рахувався тільки на вибраних роках: якщо вибрати 2003–2010 (там немає VIIRS),
    калібрувати було нема з чим, k ставав 1, і 2005 рік показував ~474 замість ~857.
    Тобто той самий рік мав різне значення залежно від положення слайдера — так не можна.

    Повертає (p, cells, daily, activity, k, viirs_start):
      p           — точки після фільтрів (зона, роки, достовірність)
      cells       — пожежо-клітинко-дні (одна клітинка сітки, що горіла в один день)
      daily       — по днях: скільки клітинок бачив MODIS, VIIRS, обидва, разом (union)
      activity    — ГОЛОВНИЙ результат: єдиний ряд активності горіння по днях
      k           — коефіцієнти калібрування для кожного місяця
      viirs_start — дата, з якої в даних з'являється VIIRS
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    # Дату початку VIIRS беремо з УСІХ даних (не лише вибраної зони),
    # бо це властивість супутника, а не регіону.
    viirs = points.loc[points["sensor"] == "VIIRS", "date"]
    viirs_start = viirs.min() if not viirs.empty else None

    # Повний період даних: від ПЕРШОГО до ОСТАННЬОГО дня, за який є хоч якісь знімки (усі зони разом).
    # На ньому рахуємо денні суми і k — незалежно від слайдера.
    # Не розтягуємо до 1 січня / 31 грудня (виправлений баг): дні, яких ще немає в даних
    # (напр. кінець поточного року), ставали "нулями", і раннє попередження на 31.12
    # показувало "0 пожеж, рівень низький", хоча насправді там просто ще немає знімків.
    full_start = points["date"].min().normalize()  # normalize() — відкидає час, лишає саму дату
    full_end = points["date"].max().normalize()

    # Фільтр БЕЗ start/end — беремо всі роки зони
    p_all = filter_points(points, bbox=bbox, min_conf=min_conf, oblasts=oblasts, exclude_war=exclude_war)
    cells_all = to_fire_cell_days(p_all, cell_deg=cell_deg)
    daily_all = daily_counts(cells_all, start=full_start, end=full_end)
    activity_all, k = harmonized_series(daily_all, viirs_start)

    # Тепер лише обрізаємо до вибраних років. .loc[start:end] для таблиці з датами в індексі
    # = "рядки з датами від start до end включно".
    p = p_all[p_all["date"].between(start, end)]
    cells = cells_all[pd.to_datetime(cells_all["date"]).between(start, end)] if not cells_all.empty else cells_all
    daily = daily_all.loc[start:end]
    activity = activity_all.loc[start:end]
    return p, cells, daily, activity, k, viirs_start
