"""
Тести (Даня — сюди додавай свої!). Запуск: pytest
"""
import pandas as pd

from firecal import analytics, harmonize, load


def _modis(rows):
    return pd.DataFrame(rows, columns=["latitude", "longitude", "brightness", "acq_date", "acq_time", "confidence", "frp", "type"])


def _viirs(rows):
    return pd.DataFrame(rows, columns=["latitude", "longitude", "bright_ti4", "acq_date", "acq_time", "confidence", "frp", "type"])


def test_confidence_classes():
    m = load.normalize(_modis([(50, 30, 320, "2020-04-01", 1000, c, 5, 0) for c in (10, 50, 90)]))
    assert m["conf_class"].tolist() == ["low", "nominal", "high"]
    v = load.normalize(_viirs([(50, 30, 330, "2020-04-01", 1000, c, 5, 0) for c in ("l", "nominal", "h")]))
    assert v["conf_class"].tolist() == ["low", "nominal", "high"]


def test_many_viirs_pixels_count_as_one_cell_day():
    """10 точок VIIRS + 1 MODIS від однієї пожежі = 1 пожежо-клітинко-день."""
    v = load.normalize(_viirs([(50.01 + i * 1e-3, 30.01, 330, "2020-04-01", 1000, "n", 5, 0) for i in range(10)]))
    m = load.normalize(_modis([(50.02, 30.02, 320, "2020-04-01", 1000, 60, 5, 0)]))
    cells = harmonize.to_fire_cell_days(pd.concat([v, m]), cell_deg=0.1)
    assert len(cells) == 1 and cells["modis"].all() and cells["viirs"].all()


def test_non_vegetation_filtered():
    m = load.normalize(_modis([(50, 30, 320, "2020-04-01", 1000, 60, 5, 2)]))  # type 2 = завод
    assert harmonize.filter_points(m).empty


def test_harmonized_uses_modis_times_k_before_viirs():
    idx = pd.date_range("2010-01-01", "2014-12-31")
    daily = pd.DataFrame({"modis": 2.0, "viirs": 3.0, "both": 1.0, "union": 4.0, "frp": 0.0}, index=idx)
    daily.loc[daily.index < "2012-01-01", ["viirs", "both", "union"]] = 0
    act, k = harmonize.harmonized_series(daily, pd.Timestamp("2012-01-01"))
    assert (k == 2.0).all()
    assert act["2010-06-01"] == 4.0 and act["2013-06-01"] == 4.0  # ряд без "сходинки" у 2012


def test_critical_period_found():
    idx = pd.date_range("2015-01-01", "2020-12-31")
    act = pd.Series(1.0, index=idx)
    act[idx.month == 4] = 50.0
    crit = analytics.critical_periods(act)
    assert not crit.empty
    assert crit.iloc[0]["частка річного горіння, %"] > 50
