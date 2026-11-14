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


def test_week_52_same_size_every_year():
    """Тиждень 52 має однакову кількість днів у різних роках (раніше в роки з ISO-тижнем 53 — 14 днів).

    2020 рік має ISO-тиждень 53. Якщо щодня горить 1, тиждень 52 у 2020 і в 2021 має бути
    приблизно однаковим (8–9 днів), а не 14 проти 7.
    """
    idx = pd.date_range("2019-01-01", "2021-12-31")
    w = analytics.weekly(pd.Series(1.0, index=idx))
    wk52 = w[w["week"] == 52].set_index("year")["value"]
    assert wk52[2020] == 9 and wk52[2021] == 8  # 2020 високосний: дні 358–366; 2021: 358–365
    assert w["week"].max() == 52  # тижня 53 немає
    assert w.groupby("year")["value"].sum().tolist() == [365, 366, 365]  # жоден день не загубився


def test_default_years_skip_partial_year():
    """Якщо дані починаються 1 листопада 2000 (як MODIS), слайдер за замовчуванням з 2001."""
    dates = pd.Series(pd.to_datetime(["2000-11-01", "2010-06-01", "2025-12-31"]))
    assert analytics.default_years(dates) == (2001, 2025)


def test_early_warning_levels():
    """Раннє попередження: звичайний квітень — низький рівень, квітень удесятеро активніший — дуже високий."""
    idx = pd.date_range("2010-01-01", "2020-12-31")
    act = pd.Series(1.0, index=idx)
    act[idx.month == 4] = 10.0  # щороку квітень активніший — це НОРМА для квітня
    # 2015: звичайний квітень -> тривоги немає, хоч горить у 10 разів більше, ніж у березні
    assert analytics.early_warning(act, "2015-04-15")["level"] == "низький"
    # 2018: квітень у 10 разів активніший за звичайний квітень -> дуже високий
    act["2018-04"] = 100.0
    ew = analytics.early_warning(act, "2018-04-15")
    assert ew["level"] == "дуже високий" and ew["percentile"] == 100
    # кінець березня: за історією, наступні 4 тижні (квітень) зазвичай активніші -> тренд > 1
    assert analytics.early_warning(act, "2015-03-31")["trend"] > 1


def test_oblast_lookup():
    """Відомі міста потрапляють у свої області; точка в Польщі — поза Україною."""
    from firecal import regions
    #              Київ (центр)   Біла Церква    Львів          Одеса          Варшава
    lat = [50.45, 49.80, 49.84, 46.48, 52.23]
    lon = [30.52, 30.11, 24.03, 30.73, 21.01]
    assert regions.oblast_of(lat, lon).tolist() == ["м. Київ", "Київська", "Львівська", "Одеська", ""]


def test_filter_by_oblast_and_war():
    """Фільтр за областю і перемикач "без прифронтових з 2022"."""
    rows = [
        (48.0, 37.8, 320, "2021-07-01", 1000, 60, 5, 0),  # Донецька, до війни
        (48.0, 37.8, 320, "2023-07-01", 1000, 60, 5, 0),  # Донецька, після 24.02.2022
        (49.84, 24.03, 320, "2023-07-01", 1000, 60, 5, 0),  # Львівська, після 24.02.2022
    ]
    pts = load.normalize(_modis(rows))
    assert len(harmonize.filter_points(pts, oblasts=["Львівська"])) == 1
    # Без прифронтових з 2022: викидається лише Донецька 2023, Донецька 2021 і Львівська лишаються
    kept = harmonize.filter_points(pts, exclude_war=True)
    assert sorted(kept["date"].dt.year.tolist()) == [2021, 2023] and len(kept) == 2


def test_critical_period_found():
    """Якщо щороку квітень у 50 разів активніший — він має стати критичним періодом."""
    idx = pd.date_range("2015-01-01", "2020-12-31")
    act = pd.Series(1.0, index=idx)  # фон: 1 щодня
    act[idx.month == 4] = 50.0       # квітень: 50 щодня
    crit = analytics.critical_periods(act)
    assert not crit.empty
    # iloc[0] — перший рядок таблиці (перший знайдений період); на нього має припадати більшість горіння
    assert crit.iloc[0]["частка річного горіння, %"] > 50


def test_series_ends_on_last_data_day():
    """Баг: дні після кінця даних ставали нулями до 31 грудня, і раннє попередження
    на 31.12 показувало "0 пожеж". Ряд має закінчуватись останнім днем, за який є знімки."""
    rows = [(50.05, 30.05, 320, d.strftime("%Y-%m-%d"), 1000, 60, 5, 0)
            for d in pd.date_range("2010-01-01", "2012-08-15")]  # щодня 1 пожежа, дані до 15.08.2012
    pts = load.normalize(_modis(rows))
    _, _, daily, act, _, _ = harmonize.run_pipeline(pts, None, "2010-01-01", "2012-12-31")
    assert act.index.max() == pd.Timestamp("2012-08-15") and daily.index.max() == pd.Timestamp("2012-08-15")
    ew = analytics.early_warning(act)  # за замовчуванням — останній день ряду
    assert ew["as_of"] == pd.Timestamp("2012-08-15") and ew["current"] == 7  # 7 днів × 1 пожежа


def test_partial_week_dropped():
    """Тиждень, обрізаний кінцем даних (3 дні з 7), не потрапляє в тижневі суми й аномалії."""
    act = pd.Series(1.0, index=pd.date_range("2019-01-01", "2021-01-17"))  # 2021: тижні 1–2 повні, 3 — 3 дні
    w = analytics.weekly(act)
    w2021 = w[w["year"] == 2021]
    assert w2021["week"].tolist() == [1, 2] and (w2021["value"] == 7).all()
    assert analytics.anomalies(act).empty  # щодня однаково -> жодних аномалій, і "тихого" 3-го тижня теж


def test_trend_does_not_peek_into_future():
    """Баг: для 20.12.2015 вікно "наступні 4 тижні" 21.12.2015–17.01.2016 рахувалось як рік 2016,
    тож у "історію" потрапляло майбутнє самої дати. Тепер рік вікна = рік дати."""
    act = pd.Series(1.0, index=pd.date_range("2010-01-01", "2020-12-31"))
    act["2015-12-21":"2016-01-17"] = 1000.0  # величезний сплеск ОДРАЗУ після вибраної дати
    nxt = analytics.window_sums(act, pd.Timestamp("2015-12-20"), 28, shift=28)
    assert nxt[2015] == 28 * 1000  # вікно після 20.12.2015 записане як 2015 — і early_warning його викине
    assert nxt[2014] == 28  # звичайний рік не зачеплено
    assert analytics.early_warning(act, "2015-12-20")["trend"] == 1.0  # сплеск не просочився в прогноз


def test_early_warning_needs_full_window():
    """Вікно, що вилазить за початок даних, не порівнюємо (раніше "зараз" рахувалось за менше днів)."""
    act = pd.Series(1.0, index=pd.date_range("2003-01-01", "2010-12-31"))
    assert analytics.early_warning(act, "2003-01-02", window=7) == {}
    assert analytics.early_warning(act, "2003-01-07", window=7)["current"] == 7


def test_combine_drops_duplicate_points():
    """Той самий файл, завантажений двічі (через сайт), не подвоює точки."""
    m = load.normalize(_modis([(50, 30, 320, "2020-04-01", 1000, 60, 5, 0), (50, 31, 320, "2020-04-01", 1000, 60, 5, 0)]))
    both = load.combine([m, m])
    assert len(both) == 2 and both.index.tolist() == [0, 1]


def test_excel_csv_semicolon_decimal_comma_bom():
    # Excel з українськими налаштуваннями чекає ";" між колонками і кому в дробах,
    # а BOM на початку — щоб кирилиця не стала «кракозябрами».
    from firecal import export
    df = pd.DataFrame({"рік": [2020], "активність": [12.5]})
    raw = export.to_excel_csv(df)
    assert raw.startswith(b"\xef\xbb\xbf")             # BOM utf-8-sig
    text = raw.decode("utf-8-sig")
    assert text.splitlines() == ["рік;активність", "2020;12,5"]


def test_map_cells_are_squares_with_true_counts():
    # Карта: кожна клітинка — квадрат розміром із клітинку, колір = справжня кількість днів
    # (без додавання сусідів, як було з density_map). Фільтр місяців працює.
    from firecal import mapgrid
    cells = pd.DataFrame({
        "lat": [50.05, 50.05, 50.05, 50.15],
        "lon": [30.05, 30.05, 30.05, 30.05],
        "date": pd.to_datetime(["2020-04-01", "2020-04-02", "2020-08-01", "2020-04-01"]),
    })
    agg = mapgrid.burn_days(cells, months=[4])           # лише квітень
    assert agg.set_index("id")["днів горіння"].to_dict() == {"50.05_30.05": 2, "50.15_30.05": 1}
    gj = mapgrid.cells_geojson(agg, cell_deg=0.1)
    ring = gj["features"][0]["geometry"]["coordinates"][0]
    assert len(gj["features"]) == 2
    assert ring[0] == ring[-1]                           # контур замкнений
    assert abs((ring[1][0] - ring[0][0]) - 0.1) < 1e-9   # ширина квадрата = клітинка 0.1°
    assert mapgrid.color_cap(pd.Series([1] * 99 + [1000])) < 1000  # викид не розтягує шкалу


def test_map_smooth_image_corners_and_transparency():
    # Плавна картинка: кути — по межах сітки з запасом, гаряча клітинка яскрава, далеко від неї прозоро.
    import base64
    import io
    import numpy as np
    from PIL import Image
    from firecal import mapgrid
    agg = pd.DataFrame({"lat": [50.05, 48.05], "lon": [30.05, 35.05], "днів горіння": [100, 100]})
    out = mapgrid.smooth_image(agg, cell_deg=0.1)
    (lon_w, lat_n), _, (lon_e, lat_s), _ = out["coordinates"]
    assert lon_w < 30.0 and lon_e > 35.1 and lat_s < 48.0 and lat_n > 50.1   # обидві клітинки всередині
    png = base64.b64decode(out["source"].split(",", 1)[1])
    alpha = np.array(Image.open(io.BytesIO(png)))[..., 3]
    assert alpha.max() > 200        # центр гарячої клітинки — майже непрозорий
    assert alpha[alpha.shape[0] // 2, alpha.shape[1] // 2] == 0   # посередині між ними — прозоро


def test_calendar_export_headers_and_excel_formatting():
    # Тижні підписуються датою першого дня, роки — колонкою "рік";
    # у .xlsx заголовки закріплені, а на числах є кольорова шкала.
    import io
    import openpyxl
    from firecal import export
    mat = pd.DataFrame([[1.25, 0.0], [3.0, 7.5]], index=[2020, 2021], columns=[1, 14])
    out = export.calendar_for_export(mat, "week", months=[])
    assert list(out.columns) == ["01.01", "02.04"]   # тиждень 14 = 1 січня + 13*7 днів = 2 квітня
    assert out.index.name == "рік"
    wb = openpyxl.load_workbook(io.BytesIO(export.to_excel(out, index=True, heatmap=True)))
    ws = wb.active
    assert ws["A1"].value == "рік" and ws["B1"].value == "01.01"
    assert ws.freeze_panes == "B2"
    assert ws["B2"].value == 1.2 and ws["B2"].number_format == "0.0"
    assert len(ws.conditional_formatting) == 1


def test_excel_header_block_above_table():
    # Шапка (заголовок + параметри) стоїть над таблицею, а заголовки таблиці й закріплення
    # зсуваються під неї. Підсумки календаря рахуються правильно.
    import io
    import openpyxl
    from firecal import export
    mat = pd.DataFrame([[1.0, 9.0], [2.0, 2.0]], index=pd.Index([2020, 2021], name="рік"), columns=[1, 14])
    summary = export.calendar_summary(mat, ["01.01", "02.04"])
    assert summary["Найактивніший рік"] == "2020 (10)"   # 1 + 9
    assert summary["Найтихіший рік"] == "2021 (4)"      # 2 + 2
    assert summary["Пік сезону (в середньому)"] == "02.04"  # середнє (9+2)/2 > (1+2)/2
    raw = export.to_excel(mat, index=True, heatmap=True, title="Календар", info={"Зона": "Україна", **summary})
    ws = openpyxl.load_workbook(io.BytesIO(raw)).active
    assert ws["A1"].value == "Календар"
    assert ws["A2"].value == "Зона" and ws["B2"].value == "Україна"
    # 1 заголовок + 5 параметрів + 1 порожній рядок = 7 рядків шапки, таблиця — з 8-го
    assert ws["A8"].value == "рік" and ws["A9"].value == 2020
    assert ws.freeze_panes == "B9"


def test_upload_rejects_non_firms_files_with_clear_message():
    # Перейменовані у .csv картинка/Excel, чужий CSV, порожній файл — не трейсбек,
    # а BadFileError з поясненням українською (app.py показує його через st.error).
    import io
    import pytest
    from PIL import Image
    from firecal import load

    def jpg_bytes():
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buf, format="JPEG")
        return buf.getvalue()

    def xlsx_bytes():
        buf = io.BytesIO()
        pd.DataFrame({"a": [1]}).to_excel(buf, index=False)
        return buf.getvalue()

    bad = {
        "photo.csv": jpg_bytes(),
        "report.csv": xlsx_bytes(),
        "empty.csv": b"",
        "other.csv": "name,price\nхліб,30\n".encode(),                       # чужий CSV
        "header_only.csv": b"latitude,longitude,acq_date,confidence,brightness\n",
        "garbage.csv": b"latitude,longitude,acq_date,confidence,brightness\nabc,30,2020-04-01,50,300\n",
    }
    for name, data in bad.items():
        with pytest.raises(load.BadFileError) as e:
            load.read_upload(name, data)
        assert name in str(e.value)                       # людина бачить, ЯКИЙ файл поганий
    # у повідомленні про колонки — перелік того, що потрібно
    with pytest.raises(load.BadFileError, match="latitude"):
        load.read_upload("other.csv", bad["other.csv"])


def test_upload_accepts_firms_csv_with_blank_time():
    # Нормальний FIRMS CSV читається; порожній acq_time не валить файл (раніше astype(int) падав на NaN).
    from firecal import load
    csv = ("LATITUDE,LONGITUDE,BRIGHTNESS,ACQ_DATE,ACQ_TIME,CONFIDENCE,FRP,TYPE\n"
           "50.1,30.1,320,2020-04-01,1035,85,12.5,0\n"
           "50.2,30.2,310,2020-04-01,,40,3.1,0\n").encode()
    df = load.read_upload("modis.csv", csv)
    assert len(df) == 2 and set(df["sensor"]) == {"MODIS"}
    assert list(df["conf_class"]) == ["high", "nominal"]
    assert list(df["time"]) == [1035, 0]


def test_upload_minimal_firms_csv_without_optional_columns():
    # Лише обов'язкові колонки (без acq_time, type, frp, satellite) — файл читається, а не падає.
    from firecal import load
    csv = b"latitude,longitude,brightness,acq_date,confidence\n50.1,30.1,320,2020-04-01,85\n"
    df = load.read_upload("min.csv", csv)
    assert len(df) == 1 and df["time"].iloc[0] == 0 and df["type"].iloc[0] == 0


def test_smooth_image_uses_given_cap():
    # Згладжена й різка картинки мають ділити одну шкалу кольорів, тож cap можна задати ззовні
    from firecal import mapgrid
    agg = pd.DataFrame({"lat": [50.05], "lon": [30.05], "днів горіння": [10]})
    out = mapgrid.smooth_image(agg, 0.1, cap=123.0)
    assert out["cap"] == 123.0