# 🔥 Календар активності горіння (MODIS + VIIRS)

Веб-додаток, що гармонізує хот-споти NASA FIRMS від датчиків MODIS і VIIRS у єдиний
календар активності горіння для вибраної зони: сезонність, критичні періоди, аномалії, карта.

> **In English.** Fire Activity Calendar harmonizes NASA FIRMS active-fire hotspots from MODIS (since 2000)
> and VIIRS (since 2012) into one continuous series of *fire-cell-days* on a common grid, and shows it as a
> calendar for a chosen area of interest (Ukraine by default): seasonal patterns, critical periods,
> anomalies, early warning and a map. Before VIIRS, MODIS is scaled by a monthly factor
> k = (MODIS ∪ VIIRS) / MODIS learned from the overlap years. The full methodology in English is in the app,
> tab «ℹ️ Методика» → *English*. Run: `pip install -r requirements.txt`, then `streamlit run app.py`.

> **Новачок у Python/git?** Почніть з [docs/NOVACHKAM.md](docs/NOVACHKAM.md) — там усе з нуля.

## Запуск
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/make_sample_data.py   # тестові дані, поки немає справжніх (див. data/README.md)
streamlit run app.py
```
Тести: `pytest -q` і `python scripts/smoke_app.py`.

## План на 2 дні
| Коли | Toksa + Макс (фронт/бек) | Ваня + Назар (дані) | Даня (тести) |
|---|---|---|---|
| Сб ранок | запуск скелета, полірування UI | скачати MODIS+VIIRS по Україні 2001–2025 у `data/raw/` | запустити додаток, список багів |
| Сб день | підключити справжні дані, швидкодія, вибір зони | перевірка якості, реальні події для валідації | тести на `firecal/`, перевірка цифр |
| Сб вечір | деплой на Streamlit Community Cloud | висновки для презентації | тест на різних зонах/роках |
| Нд | фікси, демо-сценарій | слайди: методика + знахідки | фінальний прогін демо |

Методика гармонізації — у вкладці «Методика» в додатку та в `AGENTS.md`.
