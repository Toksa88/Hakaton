"""Тести для «людських» текстів і чисел (firecal/words.py) та нових підсумків для шапки.

Схема та сама: ПІДГОТУВАТИ крихітні дані -> ВИКЛИКАТИ функцію -> ПЕРЕВІРИТИ результат.
"""
import pandas as pd

from firecal import analytics, harmonize, words

NB = words.NBSP  # нерозривний пробіл у числах: "12 345"


def test_fmt_num_ukrainian_style():
    # тисячі через пробіл, дробова частина через кому
    assert words.fmt_num(12345.6, 1) == f"12{NB}345,6"
    assert words.fmt_num(980) == "980"


def test_fmt_big_short_forms():
    assert words.fmt_big(1_630_000) == f"1,6{NB}млн"
    assert words.fmt_big(41_300) == f"41{NB}тис."
    assert words.fmt_big(980) == "980"


def test_times_words_grammar():
    # з дробом — "раза"; цілі: 21 раз, 22 рази, 25 разів, 12 разів (виняток 12–14)
    assert words.times_words(6.84) == "6,8 раза"
    assert words.times_words(21) == "21 раз"
    assert words.times_words(22) == "22 рази"
    assert words.times_words(25) == "25 разів"
    assert words.times_words(12) == "12 разів"


def test_compare_to_usual_handles_zero_norm():
    # Норма майже нуль — не пишемо "у 40 разів більше", а чесно кажемо, що зазвичай майже не горить
    assert "майже не горить" in words.compare_to_usual(40, 0)
    assert words.compare_to_usual(70, 10) == "у 7,0 раза більше, ніж зазвичай"
    assert words.compare_to_usual(5, 10) == "у 2,0 раза менше, ніж зазвичай"
    assert words.compare_to_usual(10, 10) == "приблизно як зазвичай"


def test_warning_sentence():
    ew = {"as_of": pd.Timestamp("2020-04-10"), "current": 3400, "median": 500}
    s = words.warning_sentence(ew, 7)
    assert s.startswith("За 7 дн. до 10.04.2020") and "у 6,8 раза більше, ніж зазвичай у ці дні" in s


def test_seasons_label_takes_two_biggest_in_calendar_order():
    # Три періоди: літній найбільший, весняний другий, осінній дрібний — його не показуємо.
    crit = pd.DataFrame({
        "початок": pd.to_datetime(["2021-03-20", "2021-07-05", "2021-10-01"]),
        "кінець": pd.to_datetime(["2021-04-25", "2021-08-20", "2021-10-07"]),
        "частка річного горіння, %": [30.0, 40.0, 3.0],
    })
    # весна йде першою, хоч її частка менша — підпис за календарем
    assert words.seasons_label(crit) == "бер–кві і лип–сер"


def test_critical_periods_have_dates():
    # Один гарячий тиждень на рік — він і є критичним періодом, з датами початку/кінця
    idx = pd.date_range("2018-01-01", "2020-12-31", freq="D")
    act = pd.Series(1.0, index=idx)
    act[(idx.month == 4) & (idx.day <= 7)] = 100.0
    crit = analytics.critical_periods(act)
    assert {"початок", "кінець"} <= set(crit.columns)
    assert (crit["початок"].dt.month <= 4).any()


def test_hottest_year():
    idx = pd.date_range("2018-01-01", "2020-12-31", freq="D")
    act = pd.Series(1.0, index=idx)
    act[idx.year == 2019] = 3.0  # 2019 — утричі гарячіший
    year, ratio = analytics.hottest_year(act)
    assert year == 2019 and abs(ratio - 3.0) < 0.01
    # один рік — порівнювати нема з чим
    assert analytics.hottest_year(act[act.index.year == 2018]) is None


def test_month_ticks_start_at_week_1():
    weeks, names = analytics.month_ticks()
    assert weeks[0] == 1 and names[0] == "Січ" and len(weeks) == 12
    assert weeks == sorted(weeks)  # місяці йдуть по черзі


def test_overlap_ratio():
    idx = pd.date_range("2011-12-30", "2012-01-03", freq="D")
    modis = pd.Series([5, 5, 1, 1, 1], index=idx, dtype=float)
    viirs = pd.Series([0, 0, 3, 3, 3], index=idx, dtype=float)
    # рахуємо лише з дня старту VIIRS: 9 / 3 = 3
    assert harmonize.overlap_ratio(modis, viirs, pd.Timestamp("2012-01-01")) == 3.0
    assert harmonize.overlap_ratio(modis, viirs, None) is None
    # Зона без жодної точки: порожні ряди без дат в індексі — не падаємо (знайдено AppTest-ом)
    empty = pd.Series(dtype=float)
    assert harmonize.overlap_ratio(empty, empty, pd.Timestamp("2012-01-01")) is None


def test_scale_ticks_marks_ends():
    vals, texts = words.scale_ticks(4530)
    assert vals[0] == 0 and vals[-1] == 4530
    assert texts[0] == "0 · мало" and texts[-1].endswith("+ · багато")
    assert vals == sorted(vals)


def test_ratio_text_honest_for_zero_norm():
    assert words.ratio_text(5, 0) == "зазвичай майже нуль"
    assert words.ratio_text(68, 10) == "×6,8"


def test_top_anomalies_sorts_by_difference_not_ratio():
    # Зимовий тиждень: 5 замість 0 (величезний z), весняний: 3000 замість 500 — він має бути першим
    anom = pd.DataFrame({"year": [2020, 2020, 2019], "week": [2, 14, 20], "value": [5.0, 3000.0, 10.0],
                         "norm": [0.0, 500.0, 400.0], "z": [5.0, 3.0, -2.5],
                         "kind": ["вище норми", "вище норми", "нижче норми"]})
    top = analytics.top_anomalies(anom, 10)
    assert list(top["week"]) == [14, 2]  # "нижче норми" не потрапляє в топ


def test_english_numbers_and_words():
    # Англійською — англійський формат чисел і без українських відмінків
    assert words.fmt_num(12345.6, 1, en=True) == "12,345.6"
    assert words.fmt_big(1_630_000, en=True) == "1.6M"
    assert words.fmt_big(41_300, en=True) == "41K"
    assert words.times_words(6.84, en=True) == "6.8 times"
    assert words.compare_to_usual(70, 10, en=True) == "7.0 times more than usual"
    assert words.ratio_text(5, 0, en=True) == "usually almost zero"
    ew = {"as_of": pd.Timestamp("2020-04-10"), "current": 3400, "median": 500}
    assert "6.8 times more than usual for these days" in words.warning_sentence(ew, 7, en=True)
    vals, texts = words.scale_ticks(4530, en=True)
    assert texts[0] == "0 · few" and texts[-1] == "4,530+ · many"


def test_rounding_edges():
    # 9.97 округлюється до 10 — пишемо як ціле, а не "10,0"; 999 600 — це "1,0 млн", а не "1 000 тис."
    assert words.fmt_times(9.97) == "×10"
    assert words.times_words(9.97) == "10 разів"
    assert words.fmt_big(999_600) == f"1,0{NB}млн"


def test_zone_labels_in_english():
    from firecal import regions
    # Кожна зона має англійську назву без кирилиці
    for z in regions.ZONES:
        en = regions.zone_label(z, en=True)
        assert en and not any("а" <= ch.lower() <= "я" or ch in "іїєґІЇЄҐ" for ch in en), (z, en)
    assert regions.zone_label("Сумська область", en=True) == "Sumy Oblast"
    assert regions.zone_label("м. Київ", en=True) == "Kyiv City"
    assert regions.zone_label("Сумська область") == "Сумська область"  # українською — без змін


def test_oblast_category_same_as_names():
    # Економна версія (category) дає ті самі області, що й звичайна (рядки)
    from firecal import regions
    lat = [50.45, 48.0, 0.0, 46.6]
    lon = [30.52, 37.8, 0.0, 32.6]
    assert list(regions.oblast_category(lat, lon)) == list(regions.oblast_of(lat, lon))


def test_critical_periods_empty_when_no_fires():
    # Зона без жодної пожежі: не "весь рік критичний", а порожньо
    act = pd.Series(0.0, index=pd.date_range("2018-01-01", "2020-12-31", freq="D"))
    assert analytics.critical_periods(act).empty
    assert words.seasons_label(analytics.critical_periods(act)) == "—"
