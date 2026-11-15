"""
Готує файл даних для сервера деплою: data/deploy/ukraine.parquet (лежить у git, ~30 МБ).

Навіщо: сирі CSV з FIRMS (145 МБ) у git не кладемо, а Streamlit Cloud бачить лише те, що в git.
Тому зводимо всі CSV з data/raw/ в єдину схему (як робить додаток) і зберігаємо одним стиснутим
parquet-файлом. На сервері data/raw/ порожня — і load.load_cached() читає саме цей файл.

Запуск (з папки проєкту, після scripts/download_firms.py):
    .venv/Scripts/python scripts/build_deploy_data.py
Потім закомітити data/deploy/ukraine.parquet.
"""
import sys
from pathlib import Path

# Щоб `import firecal` працював при запуску скрипта з будь-якої папки: додаємо корінь проєкту в шляхи
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firecal import load  # noqa: E402 (імпорт після sys.path — навмисно)


def main() -> int:
    files = sorted(load.RAW_DIR.glob("*.csv"))
    if not files:
        print("У data/raw/ немає CSV. Спершу: .venv/Scripts/python scripts/download_firms.py")
        return 1
    if any(f.name.startswith("SAMPLE") for f in files):
        print("У data/raw/ лежать синтетичні SAMPLE_*.csv — на сервер вони не мають потрапити. Видаліть їх.")
        return 1
    df = load.load_raw()  # усі CSV -> єдина схема, без дублікатів
    load.DEPLOY_FILE.parent.mkdir(parents=True, exist_ok=True)
    # compression="zstd" — сильніше стискає, ніж стандартний snappy, а читається так само швидко
    df.to_parquet(load.DEPLOY_FILE, index=False, compression="zstd")
    size_mb = load.DEPLOY_FILE.stat().st_size / 1e6
    print(f"Готово: {load.DEPLOY_FILE} — точок {len(df):,}, {size_mb:.1f} МБ, "
          f"дати {df['date'].min():%d.%m.%Y}–{df['date'].max():%d.%m.%Y}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
