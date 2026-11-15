"""
Зони інтересу — справжні області України замість прямокутників "на око".

Звідки межі: firecal/oblasts.npz — "розфарбована" сітка 0.01° (~1 км), де кожна клітинка
знає, в якій вона області (0 = не Україна). Будується скриптом scripts/build_oblasts.py
з відкритих кордонів geoBoundaries; перебудовувати не треба, файл уже в репо.

Як визначаємо область точки: округлюємо координати до клітинки сітки й беремо номер з таблиці.
Це миттєво навіть для 2 млн точок і не потребує геометричних бібліотек.

Команда даних: свої групи областей додавайте в ZONES нижче.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# --- Завантажуємо сітку областей один раз при імпорті модуля ---
_npz = np.load(Path(__file__).resolve().parent / "oblasts.npz")
_GRID = _npz["grid"]                       # 2D-таблиця: рядок = широта, стовпець = довгота, значення = номер області
_LON0, _LAT0 = (float(v) for v in _npz["origin"])  # координати лівого нижнього кута сітки
_STEP = float(_npz["step"])                # розмір клітинки, градуси
# Номер 0 = "не Україна", тож на місце 0 ставимо порожній рядок, а області — з 1
OBLAST_NAMES = [""] + [str(n) for n in _npz["names"]]

# Області з найактивнішими бойовими діями після 24.02.2022: там багато хот-спотів від обстрілів
# і пожеж після них, а не с/г палів. Для перемикача "без зони бойових дій".
# Список спірний (фронт рухався) — команда може уточнити.
WAR_START = pd.Timestamp("2022-02-24")
FRONTLINE = ["Донецька", "Луганська", "Запорізька", "Херсонська", "Харківська", "Миколаївська", "Сумська"]

# Готові зони: "назва в інтерфейсі -> список областей". None = вся Україна (без фільтра за областями).
ZONES: dict[str, list[str] | None] = {
    "Україна (вся)": None,
    "Південь (Херсонська, Миколаївська, Запорізька, Одеська)": ["Херсонська", "Миколаївська", "Запорізька", "Одеська"],
    "Схід (Донецька, Луганська, Харківська)": ["Донецька", "Луганська", "Харківська"],
    "Полісся (Житомирська, Київська, Чернігівська, Рівненська, Волинська)":
        ["Житомирська", "Київська", "Чернігівська", "Рівненська", "Волинська"],
    "Захід (Львівська, Закарпатська, Івано-Франківська, Тернопільська, Чернівецька)":
        ["Львівська", "Закарпатська", "Івано-Франківська", "Тернопільська", "Чернівецька"],
    "Прифронтові області (з 2022)": FRONTLINE,
}
# + кожна область окремо, за абеткою (sorted). "м. Київ" і "м. Севастополь" теж — це окремі одиниці.
ZONES.update({f"{n} область" if not n.startswith(("м.", "АР")) else n: [n] for n in sorted(OBLAST_NAMES[1:])})

# --- Англійські назви для перемикача мови. Ключ зони (українська назва) НЕ міняється — він живе в
# st.session_state["zone"] і в кнопках-прикладах; англійською лише ПОКАЗУЄМО (zone_label).
OBLAST_EN = {
    "Вінницька": "Vinnytsia", "Волинська": "Volyn", "Дніпропетровська": "Dnipropetrovsk",
    "Донецька": "Donetsk", "Житомирська": "Zhytomyr", "Закарпатська": "Zakarpattia",
    "Запорізька": "Zaporizhzhia", "Івано-Франківська": "Ivano-Frankivsk", "Київська": "Kyiv",
    "Кіровоградська": "Kirovohrad", "Луганська": "Luhansk", "Львівська": "Lviv",
    "Миколаївська": "Mykolaiv", "Одеська": "Odesa", "Полтавська": "Poltava", "Рівненська": "Rivne",
    "Сумська": "Sumy", "Тернопільська": "Ternopil", "Харківська": "Kharkiv", "Херсонська": "Kherson",
    "Хмельницька": "Khmelnytskyi", "Черкаська": "Cherkasy", "Чернівецька": "Chernivtsi",
    "Чернігівська": "Chernihiv", "м. Київ": "Kyiv City", "АР Крим": "Crimea (AR)",
    "м. Севастополь": "Sevastopol City",
}
# Групи областей — назви вручну (області в дужках — з OBLAST_EN)
_GROUP_EN = {
    "Україна (вся)": "Ukraine (all)",
    "Прифронтові області (з 2022)": "Frontline oblasts (since 2022)",
}
_GROUP_WORD_EN = {"Південь": "South", "Схід": "East", "Полісся": "Polissia", "Захід": "West"}


def oblast_label(name: str, en: bool = False) -> str:
    """Назва області для людей: "Донецька" -> "Донецька" або "Donetsk"."""
    return OBLAST_EN.get(name, name) if en else name


def zone_label(zone: str, en: bool = False) -> str:
    """Назва зони потрібною мовою. Українська — як є; англійська — перекладена.

    "Полісся (Житомирська, Київська, ...)" -> "Polissia (Zhytomyr, Kyiv, ...)",
    "Сумська область" -> "Sumy Oblast", "м. Київ" -> "Kyiv City".
    Невідома назва (напр. CUSTOM_ZONE з app.py) повертається без змін — її перекладає сам app.py.
    """
    if not en or zone not in ZONES:
        return zone
    if zone in _GROUP_EN:
        return _GROUP_EN[zone]
    word = zone.split(" (")[0]
    if word in _GROUP_WORD_EN:  # група: слово + перелік областей у дужках
        return f"{_GROUP_WORD_EN[word]} ({', '.join(OBLAST_EN[n] for n in ZONES[zone])})"
    (name,) = ZONES[zone]       # одна область
    return OBLAST_EN[name] + ("" if name.startswith(("м.", "АР")) else " Oblast")


def oblast_of(lat, lon) -> np.ndarray:
    """Назва області для кожної точки (масив рядків; "" — точка поза Україною)."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    # Номер рядка/стовпця сітки: на скільки клітинок точка відійшла від лівого нижнього кута
    row = np.floor((lat - _LAT0) / _STEP).astype(int)
    col = np.floor((lon - _LON0) / _STEP).astype(int)
    # Точки поза сіткою (напр. над морем далеко від берега) -> код 0
    inside = (row >= 0) & (row < _GRID.shape[0]) & (col >= 0) & (col < _GRID.shape[1])
    code = np.zeros(len(lat), dtype=int)
    code[inside] = _GRID[row[inside], col[inside]]
    # np.array(OBLAST_NAMES)[code] — замінюємо кожен номер на назву (як map, але для масиву)
    return np.array(OBLAST_NAMES)[code]


def bbox_of(oblasts: list[str] | None) -> tuple[float, float, float, float]:
    """Прямокутник (lon_min, lat_min, lon_max, lat_max), що охоплює вибрані області — для центру карти."""
    codes = range(1, len(OBLAST_NAMES)) if oblasts is None else [OBLAST_NAMES.index(n) for n in oblasts]
    # np.nonzero — індекси клітинок, що належать будь-якій з вибраних областей
    rows, cols = np.nonzero(np.isin(_GRID, list(codes)))
    return (_LON0 + cols.min() * _STEP, _LAT0 + rows.min() * _STEP,
            _LON0 + (cols.max() + 1) * _STEP, _LAT0 + (rows.max() + 1) * _STEP)
