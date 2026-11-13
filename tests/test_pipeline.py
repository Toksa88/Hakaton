"""
Тести (Даня — сюди додавай свої!). Запуск: pytest

Як працює pytest:
  * він сам знаходить файли test_*.py і в них функції test_*() та запускає кожну;
  * усередині тесту пишемо `assert умова` — якщо умова False, тест "червоний" (провалений);
  * тест не повинен залежати від data/raw — дані для нього створюємо тут же, крихітні й очевидні,
    щоб правильну відповідь можна було порахувати в голові.
Схема кожного тесту: ПІДГОТУВАТИ дані -> ВИКЛИКАТИ функцію -> ПЕРЕВІРИТИ результат.
"""
import pandas as pd

from firecal import analytics, harmonize, load


# Допоміжні функції (починаються з "_", тож pytest їх не запускає як тести).
# Вони роблять мінімальну "сиру" таблицю FIRMS із потрібними колонками.
# rows — список кортежів, кожен кортеж = один рядок таблиці в порядку колонок нижче.
def _modis(rows):
    # колонка brightness є лише в MODIS — за нею load.detect_sensor впізнає датчик
    return pd.DataFrame(rows, columns=["latitude", "longitude", "brightness", "acq_date", "acq_time", "confidence", "frp", "type"])


def _viirs(rows):
    # колонка bright_ti4 є лише у VIIRS
    return pd.DataFrame(rows, columns=["latitude", "longitude", "bright_ti4", "acq_date", "acq_time", "confidence", "frp", "type"])


def test_confidence_classes():
    """Достовірність MODIS (число) і VIIRS (літера/слово) зводиться до low/nominal/high."""
    # Три точки MODIS з confidence 10, 50, 90 — по одній на кожен клас (пороги 30 і 80)
    m = load.normalize(_modis([(50, 30, 320, "2020-04-01", 1000, c, 5, 0) for c in (10, 50, 90)]))
    assert m["conf_class"].tolist() == ["low", "nominal", "high"]
    # VIIRS: перевіряємо і короткий запис ("l", "h"), і довгий ("nominal")
    v = load.normalize(_viirs([(50, 30, 330, "2020-04-01", 1000, c, 5, 0) for c in ("l", "nominal", "h")]))
    assert v["conf_class"].tolist() == ["low", "nominal", "high"]


def test_many_viirs_pixels_count_as_one_cell_day():
    """10 точок VIIRS + 1 MODIS від однієї пожежі = 1 пожежо-клітинко-день."""
    # 10 точок VIIRS поруч (широта 50.010, 50.011, ... 50.019) — усі в одній клітинці 0.1°
    v = load.normalize(_viirs([(50.01 + i * 1e-3, 30.01, 330, "2020-04-01", 1000, "n", 5, 0) for i in range(10)]))
    # і одна точка MODIS у тій самій клітинці того ж дня
    m = load.normalize(_modis([(50.02, 30.02, 320, "2020-04-01", 1000, 60, 5, 0)]))
    cells = harmonize.to_fire_cell_days(pd.concat([v, m]), cell_deg=0.1)
    # Очікуємо рівно один рядок, і його бачили обидва датчики
    assert len(cells) == 1 and cells["modis"].all() and cells["viirs"].all()


def test_non_vegetation_filtered():
    """Точки з type != 0 (завод, вулкан) не мають потрапляти в аналіз пожеж."""
    m = load.normalize(_modis([(50, 30, 320, "2020-04-01", 1000, 60, 5, 2)]))  # type 2 = завод
    assert harmonize.filter_points(m).empty


def test_harmonized_uses_modis_times_k_before_viirs():
    """До появи VIIRS ряд = MODIS × k, і на межі 2012 року немає "сходинки"."""
    # Штучна денна таблиця на 2010–2014: щодня MODIS = 2, union = 4 (тобто k має вийти 4/2 = 2)
    idx = pd.date_range("2010-01-01", "2014-12-31")
    daily = pd.DataFrame({"modis": 2.0, "viirs": 3.0, "both": 1.0, "union": 4.0, "frp": 0.0}, index=idx)
    # До 2012 VIIRS "ще не літав" — обнуляємо все, що з ним пов'язане
    daily.loc[daily.index < "2012-01-01", ["viirs", "both", "union"]] = 0
    act, k = harmonize.harmonized_series(daily, pd.Timestamp("2012-01-01"))
    assert (k == 2.0).all()  # для всіх 12 місяців k = 2
    # 2010: MODIS 2 × k 2 = 4; 2013: union = 4. Однаково — отже ряд неперервний.
    assert act["2010-06-01"] == 4.0 and act["2013-06-01"] == 4.0  # ряд без "сходинки" у 2012


def test_slider_years_do_not_change_k():
    """Баг, який ми виправили: рік до 2012 має однакове значення, хоч би які роки вибрав слайдер.

    Дані: щодня у квітні 2010–2013 MODIS бачить 1 клітинку (A). З 2012 VIIRS бачить ще одну (B).
    Отже з 2012 union = 2, MODIS = 1 -> k[квітень] = 2, і квітень 2010 має бути 30 днів × 1 × 2 = 60.
    Раніше, якщо вибрати 2010–2011 (без VIIRS), k ставав 1 і квітень 2010 давав 30.
    """
    rows_m, rows_v = [], []
    for year in range(2010, 2014):
        for day in pd.date_range(f"{year}-04-01", f"{year}-04-30"):
            d = day.strftime("%Y-%m-%d")
            rows_m.append((50.05, 30.05, 320, d, 1000, 60, 5, 0))       # клітинка A, MODIS
            if year >= 2012:
                rows_v.append((50.55, 30.55, 330, d, 1000, "n", 5, 0))  # клітинка B, лише VIIRS
    points = pd.concat([load.normalize(_modis(rows_m)), load.normalize(_viirs(rows_v))], ignore_index=True)

    def april_2010(start, end):
        _, _, _, act, k, _ = harmonize.run_pipeline(points, None, start, end)
        return act["2010-04"].sum(), k[4]

    all_years = april_2010("2010-01-01", "2013-12-31")
    no_viirs_years = april_2010("2010-01-01", "2011-12-31")  # слайдер лише на роках без VIIRS
    assert all_years == (60.0, 2.0)
    assert no_viirs_years == all_years  # той самий рік — те саме число


def test_critical_period_found():
    """Якщо щороку квітень у 50 разів активніший — він має стати критичним періодом."""
    idx = pd.date_range("2015-01-01", "2020-12-31")
    act = pd.Series(1.0, index=idx)  # фон: 1 щодня
    act[idx.month == 4] = 50.0       # квітень: 50 щодня
    crit = analytics.critical_periods(act)
    assert not crit.empty
    # iloc[0] — перший рядок таблиці (перший знайдений період); на нього має припадати більшість горіння
    assert crit.iloc[0]["частка річного горіння, %"] > 50
