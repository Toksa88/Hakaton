"""
Качає справжні дані NASA FIRMS по Україні в data/raw/ — одна команда замість 40 кліків на сайті.

Запуск (з папки проєкту):
    .venv/Scripts/python scripts/download_firms.py

Що качає:
  * MODIS        за 2000–2025 (MODIS почав знімати в листопаді 2000)
  * VIIRS S-NPP  за 2012–2025
Це ті самі файли, що на https://firms.modaps.eosdis.nasa.gov/country/ -> Ukraine,
просто скрипт сам перебирає роки. Разом ~145 МБ, займає кілька хвилин.

Файли, які вже скачані, пропускає — тож якщо інтернет обірвався, просто запусти ще раз.
Тестові SAMPLE_*.csv після успішного завантаження видаляє, щоб вони не змішались зі справжніми.
"""
import sys
import urllib.request
from pathlib import Path

# Папка data/raw/ відносно цього файлу: scripts/ -> на рівень вище -> data/raw
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
BASE = "https://firms.modaps.eosdis.nasa.gov/data/country"
LAST_YEAR = 2025  # архів за поточний рік з'являється на сайті із запізненням

# Список (джерело, перший рік). Назва джерела = частина адреси і назви файлу на сайті.
SOURCES = [("modis", 2000), ("viirs-snpp", 2012)]


def main() -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    failed = []
    for source, first_year in SOURCES:
        for year in range(first_year, LAST_YEAR + 1):
            name = f"{source}_{year}_Ukraine.csv"
            target = RAW_DIR / name
            # Вже є і не порожній — не качаємо вдруге
            if target.exists() and target.stat().st_size > 0:
                print(f"є       {name}")
                continue
            url = f"{BASE}/{source}/{year}/{name}"
            try:
                # Спершу пишемо в тимчасовий .part, і лише коли скачалось повністю — перейменовуємо.
                # Так обірване завантаження не лишить "битий" CSV, який додаток спробує прочитати.
                part = target.with_suffix(".part")
                urllib.request.urlretrieve(url, part)
                part.replace(target)
                print(f"скачав  {name} ({target.stat().st_size // 1024} КБ)")
            except Exception as e:  # немає інтернету, сайт лежить, року ще немає тощо
                print(f"ПОМИЛКА {name}: {e}")
                failed.append(name)

    if failed:
        print(f"\nНе скачалось {len(failed)} файлів. Запусти скрипт ще раз — він докачає лише їх.")
        return 1

    # Усе є — прибираємо синтетику, інакше додаток змішає її зі справжніми даними
    for f in RAW_DIR.glob("SAMPLE_*.csv"):
        f.unlink()
        print(f"видалив тестовий {f.name}")
    print("\nГотово! Запускай: streamlit run app.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
