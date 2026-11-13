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
    iso = activity.index.isocalendar()
    df = pd.DataFrame({
        "year": iso["year"].to_numpy(),
        "week": np.minimum(iso["week"].to_numpy(), 52),
        "value": activity.to_numpy(),
    })
    return df.groupby(["year", "week"], as_index=False)["value"].sum()


def calendar_matrix(activity: pd.Series, freq: str = "week") -> pd.DataFrame:
    """Рядки = роки, стовпці = тиждень (1..52) або місяць (1..12)."""
    if freq == "month":
        df = pd.DataFrame({"year": activity.index.year, "col": activity.index.month, "value": activity.to_numpy()})
        return df.pivot_table(index="year", columns="col", values="value", aggfunc="sum").reindex(columns=range(1, 13))
    w = weekly(activity)
    return w.pivot_table(index="year", columns="week", values="value", aggfunc="sum").reindex(columns=range(1, 53))


def climatology(activity: pd.Series) -> pd.DataFrame:
    """Для кожного тижня року: середнє, медіана, стандартне відхилення по всіх роках."""
    w = weekly(activity)
    return w.groupby("week")["value"].agg(mean="mean", median="median", std="std", p90=lambda s: s.quantile(0.9))


def anomalies(activity: pd.Series, z_thresh: float = 2.0) -> pd.DataFrame:
    """
    Аномалія = тиждень, у якому активність відхиляється від норми цього тижня більше
    ніж на z_thresh стандартних відхилень. Використовуємо робастний z-score
    (медіана і MAD), бо пожежні дані мають викиди, а звичайне std ними "роздувається".
    """
    w = weekly(activity)
    g = w.groupby("week")["value"]
    med = g.transform("median")
    mad = g.transform(lambda s: (s - s.median()).abs().median())
    scale = (1.4826 * mad).replace(0, np.nan)
    # якщо MAD=0 (тиждень майже завжди без пожеж) — беремо std, а ще мінімум 1
    scale = scale.fillna(g.transform("std")).fillna(1.0).clip(lower=1.0)
    w["z"] = (w["value"] - med) / scale
    w["norm"] = med
    w["kind"] = np.where(w["z"] >= z_thresh, "вище норми", np.where(w["z"] <= -z_thresh, "нижче норми", ""))
    return w[w["kind"] != ""].sort_values("z", ascending=False)


def critical_periods(activity: pd.Series, top_share: float = 0.25) -> pd.DataFrame:
    """
    Критичні тижні = top 25% тижнів за середньою багаторічною активністю.
    Сусідні критичні тижні склеюємо у періоди ("тижні 12–17").
    """
    clim = climatology(activity)
    thr = clim["mean"].quantile(1 - top_share)
    crit = clim.index[clim["mean"] >= thr].tolist()
    periods, cur = [], []
    for w in crit:
        if cur and w != cur[-1] + 1:
            periods.append(cur)
            cur = []
        cur.append(w)
    if cur:
        periods.append(cur)
    total = clim["mean"].sum() or 1.0
    rows = []
    for p in periods:
        start = pd.Timestamp.fromisocalendar(2021, p[0], 1)
        end = pd.Timestamp.fromisocalendar(2021, p[-1], 7)
        rows.append({
            "тижні": f"{p[0]}–{p[-1]}" if len(p) > 1 else str(p[0]),
            "приблизно": f"{start:%d.%m} – {end:%d.%m}",
            "частка річного горіння, %": round(100 * clim.loc[p, "mean"].sum() / total, 1),
            "пік (тиждень)": int(clim.loc[p, "mean"].idxmax()),
        })
    return pd.DataFrame(rows)
