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


def weekly(activity: pd.Series) -> pd.DataFrame:
    """Денний ряд -> сума по ISO-тижнях. Тиждень 53 зливаємо з 52, щоб роки були порівнянні."""
    # isocalendar() дає для кожної дати (рік, номер тижня, день тижня) за стандартом ISO:
    # тиждень починається в понеділок, тижнів у році 52 або 53
    # Нюанс ISO: 1–3 січня можуть належати 52/53-му тижню МИНУЛОГО року, тому беремо саме
    # iso["year"], а не звичайний рік дати — інакше тиждень розірвався б між двома роками.
    iso = activity.index.isocalendar()
    df = pd.DataFrame({
        "year": iso["year"].to_numpy(),
        # np.minimum(..., 52): тиждень 53 (буває раз на 5–6 років) записуємо як 52.
        # Ціна: у такі роки "тиждень 52" містить 14 днів і виглядає вдвічі активнішим.
        "week": np.minimum(iso["week"].to_numpy(), 52),
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
        # Переводимо номери тижнів у приблизні дати: понеділок першого тижня і неділя останнього.
        # 2021 — довільний "звичайний" рік, нам потрібні лише день і місяць.
        start = pd.Timestamp.fromisocalendar(2021, p[0], 1)
        end = pd.Timestamp.fromisocalendar(2021, p[-1], 7)
        # Один рядок майбутньої таблиці: словник "назва колонки -> значення"
        rows.append({
            "тижні": f"{p[0]}–{p[-1]}" if len(p) > 1 else str(p[0]),
            "приблизно": f"{start:%d.%m} – {end:%d.%m}",
            "частка річного горіння, %": round(100 * clim.loc[p, "mean"].sum() / total, 1),
            "пік (тиждень)": int(clim.loc[p, "mean"].idxmax()),
        })
    return pd.DataFrame(rows)
