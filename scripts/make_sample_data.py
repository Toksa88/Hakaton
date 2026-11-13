"""
Генератор ТЕСТОВИХ (синтетичних!) даних у форматі FIRMS.

Навіщо: щоб фронт/бек можна було робити, поки команда даних качає справжні CSV.
Справжні дані кладемо в data/raw/ (див. data/README.md), а цей файл тоді видаляємо.

Запуск:  python scripts/make_sample_data.py
Створить data/raw/SAMPLE_modis.csv і data/raw/SAMPLE_viirs.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd

rng = np.random.default_rng(42)
OUT = Path(__file__).resolve().parent.parent / "data" / "raw"

# "гарячі" зони: (lon, lat, розкид°, вага)
ZONES = [
    (33.5, 46.8, 1.2, 3.0),   # південь — с/г випалювання
    (37.5, 48.3, 1.0, 2.0),   # схід
    (29.5, 51.2, 0.8, 1.0),   # Полісся — лісові пожежі, торфовища
    (30.0, 46.8, 0.9, 1.5),   # Одещина
    (24.5, 50.0, 1.5, 0.6),   # захід
]
VIIRS_START = pd.Timestamp("2012-01-20")


def seasonal_mean(doy: np.ndarray) -> np.ndarray:
    """Типова для України сезонність: весняні підпали трави + літні жнива."""
    spring = 9 * np.exp(-((doy - 95) / 18) ** 2)    # кінець березня – квітень
    summer = 6 * np.exp(-((doy - 215) / 20) ** 2)   # кінець липня – серпень
    autumn = 2 * np.exp(-((doy - 285) / 15) ** 2)   # жовтень
    return 0.3 + spring + summer + autumn


def main(start="2003-01-01", end="2024-12-31"):
    days = pd.date_range(start, end, freq="D")
    doy = days.dayofyear.to_numpy()
    year_factor = rng.lognormal(0, 0.25, size=days.year.nunique())
    yf = dict(zip(sorted(days.year.unique()), year_factor))
    yf[2020] *= 2.0  # штучна "аномальна весна" для демонстрації аномалій
    lam = seasonal_mean(doy) * np.array([yf[y] for y in days.year])
    lam[(days.year == 2020) & (days.month.isin([3, 4]))] *= 2.5
    n_events = rng.poisson(lam)

    zones = np.array(ZONES)
    w = zones[:, 3] / zones[:, 3].sum()
    modis_rows, viirs_rows = [], []
    for day, n in zip(days, n_events):
        if n == 0:
            continue
        z = rng.choice(len(zones), size=n, p=w)
        lon = zones[z, 0] + rng.normal(0, zones[z, 2])
        lat = zones[z, 1] + rng.normal(0, zones[z, 2] * 0.6)
        size = rng.lognormal(0, 1, size=n)  # "розмір" пожежі
        for i in range(n):
            # MODIS (1 км) пропускає дрібні пожежі
            if rng.random() < min(0.95, 0.25 + 0.3 * size[i]):
                for _ in range(1 + int(size[i] > 2)):
                    modis_rows.append((lat[i] + rng.normal(0, .005), lon[i] + rng.normal(0, .005), day,
                                       int(rng.integers(0, 2400)), rng.choice(["T", "A"]),
                                       int(np.clip(rng.normal(70, 20), 0, 100)), round(10 * size[i] + rng.random() * 5, 1)))
            # VIIRS (375 м) бачить майже все і дає більше пікселів
            if day >= VIIRS_START and rng.random() < 0.92:
                for _ in range(1 + rng.poisson(1 + 2 * size[i])):
                    viirs_rows.append((lat[i] + rng.normal(0, .004), lon[i] + rng.normal(0, .004), day,
                                       int(rng.integers(0, 2400)), "N",
                                       rng.choice(["l", "n", "h"], p=[.1, .75, .15]), round(3 * size[i] + rng.random() * 2, 1)))

    m = pd.DataFrame(modis_rows, columns=["latitude", "longitude", "acq_date", "acq_time", "satellite", "confidence", "frp"])
    m = m.assign(brightness=320.0, scan=1.0, track=1.0, instrument="MODIS", version="6.1NRT",
                 bright_t31=290.0, daynight="D", type=0)
    v = pd.DataFrame(viirs_rows, columns=["latitude", "longitude", "acq_date", "acq_time", "satellite", "confidence", "frp"])
    v = v.assign(bright_ti4=330.0, scan=0.4, track=0.4, instrument="VIIRS", version="2",
                 bright_ti5=290.0, daynight="D", type=0)
    for df in (m, v):
        df["acq_date"] = df["acq_date"].dt.strftime("%Y-%m-%d")
    OUT.mkdir(parents=True, exist_ok=True)
    m.to_csv(OUT / "SAMPLE_modis.csv", index=False)
    v.to_csv(OUT / "SAMPLE_viirs.csv", index=False)
    print(f"MODIS: {len(m):,} точок, VIIRS: {len(v):,} точок -> {OUT}")


if __name__ == "__main__":
    main()
