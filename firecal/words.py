"""Тексти й числа «для людей»: як показувати результати простою мовою.

Навіщо окремий файл: на екрані ми пишемо не «пожежо-клітинко-дні» і «робастний z», а
«осередки горіння» і «у 6 разів більше, ніж зазвичай». Усі такі перетворення — тут, щоб
однакові слова й формат чисел були на всіх вкладках, і щоб їх можна було перевірити тестами.

Дві мови. У кожної функції, що повертає текст, є параметр en: False (за замовчуванням) —
українською, True — англійською (перемикач мови в бічній панелі app.py). Числа теж
різні: українською «12 345,6», англійською «12,345.6».

Словничок (екран -> наука), він же у вкладці «Методика»:
  осередок горіння            -> пожежо-клітинко-день (клітинка сітки, де за добу був хоч один хот-спот)
  зазвичай                    -> медіана по роках
  незвично гарячий тиждень    -> робастний z ≥ 2 (медіана/MAD)
  у 9 з 10 років нижче        -> 90-й перцентиль
  поправка                    -> коефіцієнт k = union / MODIS
  достовірність низька/середня/висока -> low / nominal / high
"""
from __future__ import annotations

import pandas as pd

# Підписи місяців (індекс 0 = січень). Ті самі, що в app.MONTHS.
MONTHS = ["Січ", "Лют", "Бер", "Кві", "Тра", "Чер", "Лип", "Сер", "Вер", "Жов", "Лис", "Гру"]
MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Що показуємо людині замість технічних значень налаштувань
CONF_LABELS = {"low": "низька", "nominal": "середня", "high": "висока"}
CONF_LABELS_EN = {"low": "low", "nominal": "nominal", "high": "high"}
# Розмір клітинки сітки в градусах -> приблизний розмір у км (0.1° ≈ 8×11 км в Україні)
CELL_LABELS = {0.05: "~5 км", 0.1: "~10 км", 0.25: "~25 км", 0.5: "~50 км"}
CELL_LABELS_EN = {0.05: "~5 km", 0.1: "~10 km", 0.25: "~25 km", 0.5: "~50 km"}

# Нерозривний пробіл: "12 345" не розірветься між рядками посеред числа
NBSP = " "


def months(en: bool = False) -> list[str]:
    """Короткі назви місяців потрібною мовою."""
    return MONTHS_EN if en else MONTHS


def conf_label(code: str, en: bool = False) -> str:
    """"nominal" -> "середня" (укр) або "nominal" (англ)."""
    return (CONF_LABELS_EN if en else CONF_LABELS)[code]


def cell_label(deg: float, en: bool = False) -> str:
    """0.1 -> "~10 км" / "~10 km"."""
    return (CELL_LABELS_EN if en else CELL_LABELS)[deg]


def fmt_num(x: float, digits: int = 0, en: bool = False) -> str:
    """Число по-українськи: тисячі через пробіл, дробова частина через кому. 12345.6 -> "12 345,6".

    en=True — по-англійськи: "12,345.6" (саме так і дає Python, нічого міняти не треба).
    """
    # f"{x:,.1f}" дає "12,345.6" (американський формат). Міняємо: кома -> пробіл, крапка -> кома.
    s = f"{x:,.{digits}f}"
    return s if en else s.replace(",", NBSP).replace(".", ",")


def fmt_big(x: float, en: bool = False) -> str:
    """Велике число коротко: 1 630 000 -> "1,6 млн", 41 300 -> "41 тис.", 980 -> "980".

    Англійською: "1.6M", "41K", "980".
    """
    # Межа 999 500, а не 1 000 000: 999 600 при округленні до тисяч дав би "1 000 тис." — краще "1,0 млн"
    if abs(x) >= 999_500:
        return fmt_num(x / 1e6, 1, en) + ("M" if en else NBSP + "млн")
    if abs(x) >= 1e4:
        return fmt_num(x / 1e3, 0, en) + ("K" if en else NBSP + "тис.")
    return fmt_num(x, 0, en)


def fmt_times(r: float, en: bool = False) -> str:
    """Кратність: 6.84 -> "×6,8", 12.3 -> "×12" (великі числа без дробу — так легше читати)."""
    # round(r, 1) >= 10: 9.97 після округлення — вже 10, без дробу ("×10", а не "×10,0")
    return "×" + fmt_num(r, 0 if round(r, 1) >= 10 else 1, en)


def scale_ticks(zmax: float, en: bool = False) -> tuple[list[float], list[str]]:
    """Підписи шкали кольорів: круглі числа + «мало» внизу і «багато» вгорі.

    zmax — верх шкали (у нас 97-й перцентиль, тож найтемніший колір = «zmax і більше», звідси «+»).
    Напр. zmax = 4530 -> [0, 1000, 2000, 3000, 4000, 4530] і ["0 · мало", "1 000", ..., "4 530+ · багато"].
    """
    if not zmax > 0:
        return [0.0], ["0"]
    # Крок — "кругле" число (1, 2 або 5, помножене на 10ⁿ), щоб на шкалі було ~4 поділки
    raw = zmax / 4
    mag = 10 ** int(f"{raw:e}".split("e")[1])          # порядок числа: 1130 -> 1000
    step = min((m * mag for m in (1, 2, 5, 10) if m * mag >= raw), default=raw)
    vals = [i * step for i in range(int(zmax // step) + 1)]
    # Остання кругла поділка надто близько до верху — прибираємо, щоб підписи не налізли один на одний
    if len(vals) > 1 and zmax - vals[-1] < step * 0.5:
        vals.pop()
    vals.append(zmax)
    texts = [fmt_num(v, 0, en) for v in vals]
    texts[0] = "0 · few" if en else "0 · мало"
    texts[-1] = fmt_num(zmax, 0, en) + ("+ · many" if en else "+ · багато")
    return vals, texts


def month_range(start: pd.Timestamp, end: pd.Timestamp, en: bool = False) -> str:
    """Проміжок дат -> місяці: 22.03–29.04 -> "бер–кві", 05.07–25.07 -> "лип"."""
    # Малими літерами в обох мовах: так коротше, і підпис влазить у картку шапки
    names = [m.lower() for m in (MONTHS_EN if en else MONTHS)]
    a, b = names[start.month - 1], names[end.month - 1]
    return a if a == b else f"{a}–{b}"


def seasons_label(crit: pd.DataFrame, top: int = 2, en: bool = False) -> str:
    """Критичні періоди -> короткий підпис сезонів: "бер–кві і лип–сер".

    Беремо `top` періодів із найбільшою часткою річного горіння (дрібні уривки не показуємо),
    а в підписі ставимо їх за календарем — від весни до осені.
    """
    if crit.empty:
        return "—"
    # nlargest — рядки з найбільшими значеннями колонки; sort_values("початок") — назад у порядок дат
    best = crit.nlargest(top, "частка річного горіння, %").sort_values("початок")
    # " & " англійською: " and " довше, і підпис у картці шапки обрізався ("Mar–Apr and Jul...")
    return (" & " if en else " і ").join(month_range(s, e, en) for s, e in zip(best["початок"], best["кінець"]))


def compare_to_usual(current: float, usual: float, en: bool = False) -> str:
    """Порівняння з нормою словами: "у 6,8 раза більше, ніж зазвичай" / "приблизно як зазвичай" / ...

    Якщо зазвичай майже не горить (норма < 1), кратність безглузда ("у 40 разів більше" від нуля
    звучить страшно, хоча це 5 осередків) — тоді чесно кажемо, що зазвичай майже нуль.
    Англійською — ті самі випадки: "6.8 times more than usual" тощо.
    """
    if usual < 1:
        if en:
            return "although there is usually almost no fire" if current >= 1 else "almost no fire, as usual"
        return "хоча зазвичай у ці дні майже не горить" if current >= 1 else "як і зазвичай — майже не горить"
    r = current / usual
    if r >= 1.2:
        return f"{times_words(r, en)} more than usual" if en else f"у {times_words(r)} більше, ніж зазвичай"
    if r <= 0.8:
        if r <= 0:
            return "far less than usual" if en else "набагато менше, ніж зазвичай"
        return f"{times_words(1 / r, en)} less than usual" if en else f"у {times_words(1 / r)} менше, ніж зазвичай"
    return "about as usual" if en else "приблизно як зазвичай"


def times_words(r: float, en: bool = False) -> str:
    """Кратність словами з правильним відмінком: 6.84 -> "6,8 раза", 12 -> "12 разів", 22 -> "22 рази".

    Українська: з дробом — "раза"; з цілим — 1 раз, 2–4 рази, 5+ разів (але 12–14 — "разів").
    Англійська простіша: "6.8 times", "12 times".
    """
    if round(r, 1) < 10:  # 9.97 -> уже "10 разів", а не "10,0 раза"
        return f"{fmt_num(r, 1, en)} times" if en else f"{fmt_num(r, 1)} раза"
    n = int(round(r))
    if en:
        return f"{fmt_num(n, 0, en)} times"
    if n % 10 == 1 and n % 100 != 11:
        word = "раз"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        word = "рази"
    else:
        word = "разів"
    return f"{fmt_num(n)} {word}"


def ratio_text(value: float, usual: float, en: bool = False) -> str:
    """Колонка «у скільки разів більше» для таблиць: "×6,8" або чесне "зазвичай майже нуль"."""
    if usual < 1:
        return "usually almost zero" if en else "зазвичай майже нуль"
    return fmt_times(value / usual, en)


def warning_sentence(ew: dict, window: int, en: bool = False) -> str:
    """Одне речення для «світлофора» раннього попередження.

    Напр.: "За 7 дн. до 10.04.2020 осередків горіння — 3 400, у 6,8 раза більше, ніж зазвичай у ці дні."
    Число стоїть ПІСЛЯ слова "осередків" — так не треба узгоджувати відмінок (1 осередок / 3 осередки / 5 осередків).
    """
    cmp = compare_to_usual(ew["current"], ew["median"], en)
    if en:
        # "than usual" -> "than usual for these days": порівнюємо з тими самими днями інших років
        cmp = cmp.replace("than usual", "than usual for these days")
        return f"Fire cells in the {window} days up to {ew['as_of']:%d.%m.%Y}: {fmt_num(ew['current'], 0, en)}, {cmp}."
    # "ніж зазвичай" -> "ніж зазвичай у ці дні": уточнюємо, з чим порівнюємо (з тими самими днями інших років)
    cmp = cmp.replace("ніж зазвичай", "ніж зазвичай у ці дні")
    return f"За {window} дн. до {ew['as_of']:%d.%m.%Y} осередків горіння — {fmt_num(ew['current'])}, {cmp}."
