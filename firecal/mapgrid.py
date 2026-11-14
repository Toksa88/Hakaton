"""Дані для вкладки «Карта»: плавна картинка «де горить найчастіше» + невидимі квадрати для підказок.

Чому не px.density_map (як було): «розмита» теплова карта малює кожну клітинку плямою
фіксованого радіуса В ПІКСЕЛЯХ і ДОДАЄ плями, що накладаються. При віддаленні клітинки
0.1° стають ближчими за радіус (на зумі 4 — ~2 px одна від одної), плями сотнями
накладаються, сума впирається в максимум — і вся Україна червона, хоч дані ті самі.

Що робимо натомість: самі розмиваємо сітку В ГРАДУСАХ (smooth_image) і кладемо готову картинку
на карту. Розмиття «прив'язане до землі»: при віддаленні картинка просто зменшується, нічого
не додається — колір означає те саме на будь-якому зумі. Квадрати (cells_geojson) лишаються
невидимими поверх — щоб при наведенні мишею показувати точне число клітинки.
"""
import base64
import io

import numpy as np
import pandas as pd
from PIL import Image


def burn_days(cells: pd.DataFrame, months: list[int]) -> pd.DataFrame:
    """Пожежо-клітинко-дні -> скільки днів горіла кожна клітинка у вибрані місяці.

    Повертає колонки: id (рядок-ідентифікатор квадрата для geojson), lat, lon (центр), «днів горіння».
    """
    cm = cells[cells["date"].dt.month.isin(months)]  # лишаємо лише вибрані місяці
    # groupby + size: для кожної пари (lat, lon) — кількість рядків, тобто днів горіння
    agg = cm.groupby(["lat", "lon"], as_index=False).size().rename(columns={"size": "днів горіння"})
    # id — щоб зв'язати рядок таблиці з його квадратом у geojson (round — щоб не було 50.150000000001)
    agg["id"] = agg["lat"].round(4).astype(str) + "_" + agg["lon"].round(4).astype(str)
    return agg


def cells_geojson(agg: pd.DataFrame, cell_deg: float) -> dict:
    """Квадрати клітинок у форматі GeoJSON (так plotly розуміє, які фігури малювати на карті).

    Кожен квадрат — центр ± пів клітинки. Координати в GeoJSON пишуться як [довгота, широта],
    і контур замикається: остання точка = перша.
    """
    h = cell_deg / 2
    features = []
    for cid, lat, lon in zip(agg["id"], agg["lat"], agg["lon"]):
        ring = [[lon - h, lat - h], [lon + h, lat - h], [lon + h, lat + h], [lon - h, lat + h], [lon - h, lat - h]]
        features.append({"type": "Feature", "id": cid, "properties": {},
                         "geometry": {"type": "Polygon", "coordinates": [ring]}})
    return {"type": "FeatureCollection", "features": features}


# Шкала кольорів «з прозорим низом» для карти. Формат plotly: [частка шкали 0..1, колір].
# rgba(червоний, зелений, синій, непрозорість): непрозорість 0 = клітинку не видно, 1 = суцільний колір.
# Навіщо: за 25 років хоч раз горіла майже кожна клітинка України, і з суцільною шкалою квадрати
# вкривають усю країну. Тут нижня третина шкали (≈ половина клітинок — звичайний фон) прозора,
# і кольором проступають лише місця, де горить помітно частіше. Дані не викидаються:
# тихі клітинки є, просто непомітні; навівши мишу, їхнє число видно в підказці.
FADE_SCALE = [
    [0.00, "rgba(255,237,160,0)"],
    [0.35, "rgba(254,217,118,0)"],     # до ~35% шкали — повністю прозоро
    [0.50, "rgba(253,141,60,0.55)"],   # далі колір поступово проявляється
    [0.70, "rgba(240,59,32,0.85)"],
    [1.00, "rgba(189,0,38,0.95)"],
]


def color_cap(values: pd.Series, q: float = 0.99) -> float:
    """Верх шкали кольорів: 99-й перцентиль, а не максимум.

    Днів горіння дуже нерівно: медіана ~85, а кілька клітинок (полігони, торфовища) — понад 1000.
    Якщо шкала до максимуму, майже все стане блідо-жовтим. Клітинки вище перцентиля
    просто будуть найтемнішими червоними.
    """
    return max(1.0, float(values.quantile(q))) if len(values) else 1.0


def _gauss_blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """Гаусове розмиття 2D-масиву (кожен піксель = зважене середнє сусідів, ближчі важать більше).

    Робимо в два проходи — спершу по рядках, потім по стовпцях (так у сотні разів швидше, ніж
    квадратним ядром, а результат той самий). np.convolve(..., "same") — згортка: «проводимо»
    ядро вздовж рядка і в кожній точці рахуємо зважену суму.
    """
    r = max(1, int(3 * sigma))                     # далі 3 сигм вага майже нуль — обрізаємо ядро
    x = np.arange(-r, r + 1)
    kernel = np.exp(-x ** 2 / (2 * sigma ** 2))
    kernel /= kernel.sum()                         # сума ваг = 1, щоб не змінювати рівень значень
    a = np.apply_along_axis(lambda row: np.convolve(row, kernel, "same"), 1, a)
    return np.apply_along_axis(lambda col: np.convolve(col, kernel, "same"), 0, a)


def _scale_rgba(t: np.ndarray) -> np.ndarray:
    """Частки шкали 0..1 -> кольори RGBA (0..255) за FADE_SCALE, з плавним переходом між точками."""
    stops = [s for s, _ in FADE_SCALE]
    # "rgba(254,217,118,0.55)" -> [254, 217, 118, 0.55]
    comps = [[float(v) for v in c[5:-1].split(",")] for _, c in FADE_SCALE]
    out = np.zeros(t.shape + (4,), dtype=np.uint8)
    for ch in range(4):
        vals = [c[ch] * (255 if ch == 3 else 1) for c in comps]   # прозорість 0..1 -> 0..255
        out[..., ch] = np.interp(t, stops, vals).astype(np.uint8)  # interp — лінійно між сусідніми точками
    return out


def smooth_image(agg: pd.DataFrame, cell_deg: float, upsample: int = 4, sigma_cells: float = 0.8,
                 cap: float | None = None) -> dict:
    """Дні горіння клітинок -> плавна картинка PNG для накладання на карту.

    Кроки:
    1. Кладемо значення клітинок у 2D-масив (рядок = широта, стовпець = довгота), порожні = 0.
    2. Дрібнимо кожну клітинку на upsample×upsample пікселів і розмиваємо (sigma_cells — радіус
       розмиття в клітинках): квадратики зливаються в плавні плями.
    3. Переводимо в кольори FADE_SCALE (тихі місця прозорі) і кодуємо в PNG.
    4. Карта в проєкції Меркатора: смуга широти на півночі виходить «вищою», ніж на півдні.
       Тому рядки картинки беремо рівномірно в координатах Меркатора, а не в градусах, —
       інакше плями з'їхали б на кілька кілометрів від справжнього місця.

    Повертає: source — картинка як data-URI (текст, який розуміє карта), coordinates — кути
    картинки [довгота, широта] (ПнЗх, ПнСх, ПдСх, ПдЗх), cap — верх шкали кольорів.
    """
    pad = 3  # запас по краях у клітинках, щоб розмиття не обрізалось
    ix = np.round(agg["lon"].to_numpy() / cell_deg - 0.5).astype(int)  # номер стовпця клітинки
    iy = np.round(agg["lat"].to_numpy() / cell_deg - 0.5).astype(int)  # номер рядка клітинки
    x0, y0 = ix.min() - pad, iy.min() - pad
    nx, ny = ix.max() + pad - x0 + 1, iy.max() + pad - y0 + 1
    grid = np.zeros((ny, nx))
    grid[iy - y0, ix - x0] = agg["днів горіння"].to_numpy()
    grid = grid[::-1]  # рядок 0 картинки — ПІВНІЧ (верх), а в масиві він був півднем

    # np.kron з блоком одиниць — кожна клітинка стає квадратом upsample×upsample однакових пікселів
    fine = _gauss_blur(np.kron(grid, np.ones((upsample, upsample))), sigma_cells * upsample)

    # Межі картинки в градусах
    lon_min, lon_max = x0 * cell_deg, (x0 + nx) * cell_deg
    lat_min, lat_max = y0 * cell_deg, (y0 + ny) * cell_deg

    # Рядки рівномірно в Меркаторі: y = ln(tan(π/4 + φ/2)); назад φ = 2·atan(e^y) − π/2
    merc = lambda lat: np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))  # noqa: E731
    h = fine.shape[0]
    ys = merc(lat_max) - (np.arange(h) + 0.5) * (merc(lat_max) - merc(lat_min)) / h
    lats = np.degrees(2 * np.arctan(np.exp(ys)) - np.pi / 2)
    rows = np.clip(((lat_max - lats) / (lat_max - lat_min) * h).astype(int), 0, h - 1)
    fine = fine[rows]

    # Верх шкали — 99-й перцентиль серед місць, де щось горіло (розмиття знижує піки, тож рахуємо після)
    # cap можна передати ззовні: тоді дві картинки (згладжена й різка) ділять одну шкалу кольорів.
    if cap is None:
        cap = color_cap(pd.Series(fine[fine > 0.5].ravel()))
    img = Image.fromarray(_scale_rgba(np.clip(fine / cap, 0, 1)), mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    return {"source": uri, "cap": cap,
            "coordinates": [[lon_min, lat_max], [lon_max, lat_max], [lon_max, lat_min], [lon_min, lat_min]]}
