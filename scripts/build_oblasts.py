"""
Будує firecal/oblasts.npz — "розфарбовану" карту областей України.

Запускати НЕ треба: готовий файл уже лежить у репо. Цей скрипт — щоб було видно, звідки він узявся,
і щоб можна було перебудувати (напр. з іншою точністю).
    .venv/Scripts/python scripts/build_oblasts.py

Ідея (простіше, ніж геометрія з окремою бібліотекою):
  * беремо кордони областей (GeoJSON з geoBoundaries — відкрита база адмінкордонів);
  * накладаємо на Україну сітку 0.01° (~1 км) і для кожної клітинки записуємо номер області,
    в яку потрапляє її центр (0 = не Україна);
  * потім будь-яку точку FIRMS відносимо до області миттєво: округлили координати -> глянули в сітку.
Точність ~1 км на кордонах областей — для пожеж більш ніж достатньо.
"""
import io
import json
import urllib.request
from pathlib import Path

import numpy as np

URL = ("https://github.com/wmgeolab/geoBoundaries/raw/main/releaseData/gbOpen/"
       "UKR/ADM1/geoBoundaries-UKR-ADM1_simplified.geojson")
OUT = Path(__file__).resolve().parent.parent / "firecal" / "oblasts.npz"

STEP = 0.01                    # розмір клітинки, градуси
LON0, LON1 = 22.0, 40.3        # межі сітки (з запасом навколо України)
LAT0, LAT1 = 44.3, 52.4

# Назви з geoBoundaries (англійською) -> українські назви для інтерфейсу.
# Порядок тут = номери областей у сітці (1, 2, 3, ...).
NAMES = {
    "Vinnytsia Oblast": "Вінницька", "Volyn Oblast": "Волинська", "Dnipropetrovsk Oblast": "Дніпропетровська",
    "Donetsk Oblast": "Донецька", "Zhytomyr Oblast": "Житомирська", "Zakarpattia Oblast": "Закарпатська",
    "Zaporizhia Oblast": "Запорізька", "Ivano-Frankivsk Oblast": "Івано-Франківська", "Kyiv Oblast": "Київська",
    "Kirovohrad Oblast": "Кіровоградська", "Luhansk Oblast": "Луганська", "Lviv Oblast": "Львівська",
    "Mykolaiv Oblast": "Миколаївська", "Odessa Oblast": "Одеська", "Poltava Oblast": "Полтавська",
    "Rivne Oblast": "Рівненська", "Sumy Oblast": "Сумська", "Ternopil Oblast": "Тернопільська",
    "Kharkiv Oblast": "Харківська", "Kherson Oblast": "Херсонська", "Khmelnytskyi Oblast": "Хмельницька",
    "Cherkasy Oblast": "Черкаська", "Chernivtsi Oblast": "Чернівецька", "Chernihiv Oblast": "Чернігівська",
    "Kyiv": "м. Київ", "Autonomous Republic of Crimea": "АР Крим", "Sevastopol": "м. Севастополь",
}


def rings(geom):
    """Усі замкнені контури (зовнішні й "дірки") полігону чи мультиполігону як масиви (lon, lat)."""
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    return [np.asarray(r, dtype=float) for p in polys for r in p]


def fill(mask, ring_list, lons, lats):
    """
    Заливка "сканлайном": для кожного рядка сітки (широти) знаходимо, де горизонтальна лінія
    перетинає контури, сортуємо ці точки і заливаємо проміжки між 1-ю і 2-ю, 3-ю і 4-ю, ...
    Так працює правило "парності": точка всередині, якщо зліва від неї непарна кількість перетинів.
    Дірки (напр. м. Київ всередині Київської області) обробляються автоматично.
    """
    # Усі ребра всіх контурів разом: (x1, y1) -> (x2, y2)
    x1 = np.concatenate([r[:-1, 0] for r in ring_list]); y1 = np.concatenate([r[:-1, 1] for r in ring_list])
    x2 = np.concatenate([r[1:, 0] for r in ring_list]); y2 = np.concatenate([r[1:, 1] for r in ring_list])
    for i, lat in enumerate(lats):
        # Ребра, які перетинають цю широту (півінтервал, щоб вершини не рахувались двічі)
        hit = (y1 <= lat) != (y2 <= lat)
        if not hit.any():
            continue
        xs = np.sort(x1[hit] + (lat - y1[hit]) * (x2[hit] - x1[hit]) / (y2[hit] - y1[hit]))
        for a, b in zip(xs[0::2], xs[1::2]):
            mask[i, (lons >= a) & (lons < b)] = True


def main():
    with urllib.request.urlopen(URL) as r:
        gj = json.load(io.TextIOWrapper(r, encoding="utf-8"))
    # Центри клітинок сітки
    lons = LON0 + STEP * (np.arange(round((LON1 - LON0) / STEP)) + 0.5)
    lats = LAT0 + STEP * (np.arange(round((LAT1 - LAT0) / STEP)) + 0.5)
    grid = np.zeros((len(lats), len(lons)), dtype=np.uint8)  # 0 = не Україна

    order = list(NAMES)
    for f in gj["features"]:
        name = f["properties"]["shapeName"]
        code = order.index(name) + 1  # падає з помилкою, якщо в джерелі з'явилась нова назва — це добре
        mask = np.zeros_like(grid, dtype=bool)
        fill(mask, rings(f["geometry"]), lons, lats)
        grid[mask & (grid == 0)] = code  # не перефарбовуємо вже зайняте (м. Київ проти Київської)
        print(f"{NAMES[name]:<18} {mask.sum():>7} клітинок")

    np.savez_compressed(OUT, grid=grid, names=np.array([NAMES[n] for n in order]),
                        origin=np.array([LON0, LAT0]), step=np.array(STEP))
    print(f"\nЗбережено {OUT} ({OUT.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
