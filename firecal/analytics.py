"""
Крок 3. Календар активності горіння + аналітика.

  * calendar_matrix  — таблиця "рік × тиждень" для теплової карти (головний візуал)
  * climatology      — "нормальний" рівень горіння для кожного тижня року
  * anomalies        — тижні, що сильно відхиляються від норми (незвичайні умови)
  * critical_periods — тижні року, коли горить найбільше (сезон пожеж)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def week_of_year(dates: pd.DatetimeIndex) -> np.ndarray:
    """
    Номер тижня 1..52, рахуючи від 1 січня: дні 1–7 -> тиждень 1, дні 8–14 -> тиждень 2, ...
    Останні 1–2 дні року (дні 365–366) додаємо до тижня 52.

    Чому НЕ ISO-тижні (як у календарі з понеділка): у ISO раз на 5–6 років буває тиждень 53.
    Раніше ми зливали його з 52-м, і в такі роки "тиждень 52" мав 14 днів — удвічі більше
    горіння, ніж зазвичай, і хибна аномалія. Тут тиждень 52 у БУДЬ-ЯКОМУ році має 8–9 днів,
    тож роки однаково порівнянні. Плюс: тиждень не "перелазить" у сусідній рік.
    """
    # day_of_year — номер дня в році (1 січня = 1). (день - 1) // 7 + 1 — у який семиденний "кошик" він падає.
    # np.minimum(..., 52) — дні 365 і 366 потрапили б у "тиждень 53", тож записуємо їх у 52.
    return np.minimum((dates.day_of_year.to_numpy() - 1) // 7 + 1, 52)


def week_start(year: int, week: int) -> pd.Timestamp:
    """Перший день тижня `week` у році `year` (за нашою нумерацією з 1 січня)."""
    # 1 січня + 7 днів на кожен попередній тиждень. Напр. тиждень 3 -> 1 січня + 14 днів = 15 січня.
    return pd.Timestamp(int(year), 1, 1) + pd.Timedelta(days=7 * (int(week) - 1))


def default_years(dates: pd.Series) -> tuple[int, int]:
    """
    Роки для слайдера за замовчуванням: лише ПОВНІ роки на краях даних.
    Напр. MODIS почав знімати 1 листопада 2000 — 2000 рік має лише 2 місяці і на графіку
    виглядає як "провал". Такий рік за замовчуванням не показуємо (його можна вибрати вручну).
    Рік вважаємо неповним, якщо дані починаються пізніше 31 січня або закінчуються раніше 1 грудня.
    """
    first, last = dates.min(), dates.max()
    y0 = first.year + 1 if first > pd.Timestamp(first.year, 1, 31) else first.year
    y1 = last.year - 1 if last < pd.Timestamp(last.year, 12, 1) else last.year
    # Якщо даних менше ніж на рік, "повних" років немає — показуємо все, що є
    if y0 > y1:
        return first.year, last.year
    return y0, y1


def weekly(activity: pd.Series) -> pd.DataFrame:
    """Денний ряд -> суми по тижнях року (1..52, див. week_of_year)."""
    df = pd.DataFrame({
        "year": activity.index.year,
        "week": week_of_year(activity.index),
        "value": activity.to_numpy(),
    })
    # Групуємо дні за парою (рік, тиждень) і сумуємо: 7 денних значень -> одне тижневе.
    # as_index=False — year і week лишаються звичайними колонками, а не стають індексом.
    return df.groupby(["year", "week"], as_index=False)["value"].sum()


def calendar_matrix(activity: pd.Series, freq: str = "week") -> pd.DataFrame:
    """Рядки = роки, стовпці = тиждень (1..52) або місяць (1..12)."""
    if freq == "month":
        df = pd.DataFrame({"year": activity.index.year, "col": activity.index.month, "value": activity.to_numpy()})
        # pivot_table "розвертає" довгу таблицю в матрицю: рядки = роки, стовпці = місяці, у клітинках — сума
        # reindex(columns=range(1, 13)) — гарантуємо всі 12 стовпців у правильному порядку,
        # навіть якщо якогось місяця в даних немає (тоді там буде порожньо, а не зсув колонок)
        return df.pivot_table(index="year", columns="col", values="value", aggfunc="sum").reindex(columns=range(1, 13))
    # Те саме для тижнів: спершу денні значення -> тижневі суми, потім "розвертаємо" в матрицю 52 стовпці
    w = weekly(activity)
    return w.pivot_table(index="year", columns="week", values="value", aggfunc="sum").reindex(columns=range(1, 53))


def climatology(activity: pd.Series) -> pd.DataFrame:
    """Для кожного тижня року: середнє, медіана, стандартне відхилення по всіх роках."""
    w = weekly(activity)
    # Для кожного номера тижня беремо значення з усіх років і рахуємо статистики.
    # p90 = 90-й перцентиль: лише 10% років були вищими за це значення.
    return w.groupby("week")["value"].agg(mean="mean", median="median", std="std", p90=lambda s: s.quantile(0.9))


def anomalies(activity: pd.Series, z_thresh: float = 2.0) -> pd.DataFrame:
    """
    Аномалія = тиждень, у якому активність відхиляється від норми цього тижня більше
    ніж на z_thresh стандартних відхилень. Використовуємо робастний z-score
    (медіана і MAD), бо пожежні дані мають викиди, а звичайне std ними "роздувається".
    """
    w = weekly(activity)
    # g — "згруповані значення": для кожного номера тижня (1..52) — значення цього тижня в усіх роках
    g = w.groupby("week")["value"]
    # transform, на відміну від agg, повертає значення для КОЖНОГО рядка:
    # кожен (рік, тиждень) отримує медіану свого тижня по всіх роках — з нею і порівнюємо
    med = g.transform("median")
    # MAD = медіана абсолютних відхилень від медіани — "стійкий" аналог стандартного відхилення
    mad = g.transform(lambda s: (s - s.median()).abs().median())
    # 1.4826 — стандартний множник: для "нормальних" даних 1.4826 × MAD ≈ звичайне std,
    # тож поріг z = 2 означає те саме, що й у звичайному z-score, але викиди його не роздувають.
    # replace(0, nan) — позначаємо нульовий розкид як "немає", щоб нижче замінити його чимось розумним
    scale = (1.4826 * mad).replace(0, np.nan)
    # якщо MAD=0 (тиждень майже завжди без пожеж) — беремо std, а ще мінімум 1
    # (мінімум 1 — щоб одна-дві пожежі в "порожній" тиждень не давали величезний z через ділення на ~0)
    scale = scale.fillna(g.transform("std")).fillna(1.0).clip(lower=1.0)
    # z = на скільки "типових розкидів" цей тиждень вище (+) чи нижче (-) за норму
    w["z"] = (w["value"] - med) / scale
    w["norm"] = med
    # np.where(умова, якщо_так, якщо_ні) — "if" для цілого стовпчика. Вкладений where дає три варіанти:
    # z >= 2 -> "вище норми", z <= -2 -> "нижче норми", інакше "" (звичайний тиждень)
    w["kind"] = np.where(w["z"] >= z_thresh, "вище норми", np.where(w["z"] <= -z_thresh, "нижче норми", ""))
    # Лишаємо лише аномальні тижні, найбільші перевищення — зверху
    return w[w["kind"] != ""].sort_values("z", ascending=False)


def _same_day(ts: pd.Timestamp, year: int) -> pd.Timestamp:
    """Та сама дата (день і місяць) в іншому році. 29 лютого в невисокосний рік -> 28 лютого."""
    try:
        return ts.replace(year=year)
    except ValueError:  # 29 лютого не існує в цьому році
        return ts.replace(year=year, day=28)


def window_sums(activity: pd.Series, end: pd.Timestamp, days: int) -> pd.Series:
    """
    Сума активності за `days` днів, що закінчуються датою `end` (включно), — у КОЖНОМУ році даних.
    Повертає Series: індекс = рік, значення = сума за ці календарні дні того року.
    Напр. end = 10.04.2020, days = 7 -> для кожного року сума за 04.04–10.04.
    Роки, де вікно не повністю покрите даними, пропускаємо — інакше вони виглядали б "тихими".
    """
    out = {}
    for y in sorted(set(activity.index.year)):
        e = _same_day(end, y)
        s = e - pd.Timedelta(days=days - 1)
        # Вікно має бути цілком усередині даних (не обрізане початком/кінцем ряду)
        if s < activity.index.min() or e > activity.index.max():
            continue
        # .loc[s:e] — усі дні від s до e включно
        out[y] = activity.loc[s:e].sum()
    return pd.Series(out, dtype=float)


# Рівні тривоги: (назва, колір для інтерфейсу, пояснення)
LEVELS = {
    "низький": ("🟢", "нижче або на рівні звичайного для цієї пори року"),
    "підвищений": ("🟡", "вище, ніж у більшості років у цей час"),
    "високий": ("🟠", "серед найвищих 10% років для цієї пори"),
    "дуже високий": ("🔴", "значно вище за все звичайне для цієї пори (робастний z ≥ 2)"),
}


def early_warning(activity: pd.Series, as_of=None, window: int = 7, horizon: int = 28) -> dict:
    """
    Раннє попередження: наскільки ЗАРАЗ горить більше/менше, ніж зазвичай у цю пору року,
    і чого чекати далі за історією.

    1. "Зараз" = сума за останні `window` днів до дати as_of (за замовч. — останній день даних).
    2. "Норма" = ті самі календарні дні в ІНШИХ роках (поточний рік не порівнюємо сам із собою).
    3. Рівень: за перцентилями і робастним z (медіана/MAD — як в anomalies).
    4. Прогноз сезону: у скільки разів наступні `horizon` днів зазвичай активніші за останні `horizon`
       (медіана по роках). Це НЕ прогноз погоди — лише "зазвичай у цей час сезон наростає/спадає".
    """
    if activity.empty:
        return {}
    as_of = pd.Timestamp(as_of) if as_of is not None else activity.index.max()
    cur_year = as_of.year

    # 1–2. Поточне вікно і ті самі дні в інші роки
    sums = window_sums(activity, as_of, window)
    current = float(activity.loc[as_of - pd.Timedelta(days=window - 1):as_of].sum())
    base = sums.drop(cur_year, errors="ignore")  # drop — прибрати поточний рік; errors="ignore" — якщо його нема
    if base.empty:
        return {}

    med = float(base.median())
    p75, p90 = float(base.quantile(0.75)), float(base.quantile(0.9))
    # Робастний z, як в anomalies: (значення - медіана) / (1.4826 × MAD), мінімум 1 у знаменнику
    mad = float((base - med).abs().median())
    scale = max(1.4826 * mad, 1.0)
    z = (current - med) / scale
    # Частка інших років, у яких у цей час горіло МЕНШЕ, ніж зараз (0–100%)
    pct = float((base < current).mean() * 100)

    # 3. Рівень тривоги — від найсерйознішого до найлегшого, перший, що спрацював
    if z >= 2:
        level = "дуже високий"
    # Строго ">" — щоб "рівно як у звичайний рік" (коли всі роки однакові) не вважалось тривогою
    elif current > p90:
        level = "високий"
    elif current > p75:
        level = "підвищений"
    else:
        level = "низький"

    # 4. Прогноз сезону: медіана (наступні horizon днів) / медіана (попередні horizon днів) по інших роках
    nxt = window_sums(activity, as_of + pd.Timedelta(days=horizon), horizon).drop(cur_year, errors="ignore")
    prv = window_sums(activity, as_of, horizon).drop(cur_year, errors="ignore")
    common = nxt.index.intersection(prv.index)  # лише роки, де є обидва вікна
    trend = float(nxt[common].median() / prv[common].median()) if len(common) and prv[common].median() > 0 else None

    return {
        "as_of": as_of,
        "current": current,
        "median": med,
        "p90": p90,
        "z": z,
        "percentile": pct,
        "level": level,
        "trend": trend,
        "by_year": sums,  # для графіка: те саме вікно в кожному році
    }


def critical_periods(activity: pd.Series, top_share: float = 0.25) -> pd.DataFrame:
    """
    Критичні тижні = top 25% тижнів за середньою багаторічною активністю.
    Сусідні критичні тижні склеюємо у періоди ("тижні 12–17").
    """
    clim = climatology(activity)
    thr = clim["mean"].quantile(1 - top_share)  # поріг: 75-й перцентиль середньої активності
    # clim.index — номери тижнів; лишаємо ті, де середня активність не нижча за поріг
    crit = clim.index[clim["mean"] >= thr].tolist()
    # Склеюємо послідовні номери тижнів: [11,12,13,29,30] -> [[11,12,13],[29,30]]
    # periods — готові періоди, cur — період, який зараз "набираємо".
    periods, cur = [], []
    for w in crit:
        # cur[-1] — останній тиждень поточного періоду. Якщо новий тиждень не йде одразу за ним —
        # період закінчився: зберігаємо його і починаємо новий.
        if cur and w != cur[-1] + 1:
            periods.append(cur)
            cur = []
        cur.append(w)
    if cur:
        periods.append(cur)  # не забути останній період після циклу
    # Сума середніх по всіх тижнях = "типовий річний обсяг горіння". `or 1.0` — захист від ділення на 0.
    total = clim["mean"].sum() or 1.0
    rows = []
    for p in periods:
        # Переводимо номери тижнів у приблизні дати: перший день першого тижня і останній день останнього.
        # 2021 — довільний невисокосний рік, нам потрібні лише день і місяць.
        start = week_start(2021, p[0])
        end = week_start(2021, p[-1]) + pd.Timedelta(days=6)
        # Один рядок майбутньої таблиці: словник "назва колонки -> значення"
        rows.append({
            "тижні": f"{p[0]}–{p[-1]}" if len(p) > 1 else str(p[0]),
            "приблизно": f"{start:%d.%m} – {end:%d.%m}",
            "частка річного горіння, %": round(100 * clim.loc[p, "mean"].sum() / total, 1),
            "пік (тиждень)": int(clim.loc[p, "mean"].idxmax()),
        })
    return pd.DataFrame(rows)
