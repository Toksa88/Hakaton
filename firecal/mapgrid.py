"""Дані для вкладки «Карта»: кожна клітинка сітки — окремий квадрат зі своїм кольором.

Чому не px.density_map (як було): «розмита» теплова карта малює кожну клітинку плямою
фіксованого радіуса В ПІКСЕЛЯХ і ДОДАЄ плями, що накладаються. При віддаленні клітинки
0.1° стають ближчими за радіус (на зумі 4 — ~2 px одна від одної), плями сотнями
накладаються, сума впирається в максимум — і вся Україна червона, хоч дані ті самі.
Квадрати ж мають розмір у ГРАДУСАХ: масштабуються разом з картою і не додаються,
тож колір = справжня кількість днів горіння цієї клітинки на будь-якому зумі.
"""
import pandas as pd


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


def color_cap(values: pd.Series, q: float = 0.99) -> float:
    """Верх шкали кольорів: 99-й перцентиль, а не максимум.

    Днів горіння дуже нерівно: медіана ~85, а кілька клітинок (полігони, торфовища) — понад 1000.
    Якщо шкала до максимуму, майже все стане блідо-жовтим. Клітинки вище перцентиля
    просто будуть найтемнішими червоними.
    """
    return max(1.0, float(values.quantile(q))) if len(values) else 1.0
