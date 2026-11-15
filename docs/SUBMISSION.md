# Здача на NASA Space Apps — чернетка текстів

> Дедлайн: **неділя, 15.11.2026, 23:59** за місцевим часом (коментар Вані в issue #20).
> Здаємо на офіційному сайті NASA Space Apps Challenge, сторінка Project. Усе — **англійською**.
> Обов'язково: відео до 30 с **або** презентація на 7 слайдів.
> Критерії журі: Impact, Creativity, Validity, Relevance, Presentation.
>
> Нижче — готові тексти для кожного поля форми. Перевірте цифри й посилання перед вставкою.
> Цифри узгоджені зі слайдами (Google Slides): 2000–2025, середнє ~40 тис., 2008 ≈ ×2, 1,6 млн → 1,0 млн,
> k по місяцях ×2,3…×6,6 (достовірність «середня+», клітинка 0.1°). Без неповного 2000 року (як у додатку
> за замовчуванням) — ~41 тис. і ×1,9: міняєте одне — міняйте всюди.
> Місця, які треба заповнити, позначені `TODO`.

---

## Поля форми

### Project name
Fire Activity Calendar: MODIS + VIIRS, harmonized

### Short description
A web app that merges NASA FIRMS active-fire hotspots from MODIS (since 2000) and VIIRS (since 2012)
into one consistent fire-activity calendar for any area of Ukraine, so that years can finally be
compared fairly. It shows when fires usually happen, which weeks were unusual, the critical
periods of the year, and an early-warning level for any date.

### Demo link
https://fire.metalgearbot.fun

(Наш VPS, HTTPS, перемикач мови Українська / English угорі бічної панелі. Як оновлювати — `docs/DEPLOY.md`.)

### Final project link
https://github.com/Toksa88/Hakaton

(Репо публічне. Перевірено 15.11: у всій історії git немає ключів і токенів, `.env`/`secrets.toml` не в git.)

### Detailed description
**The problem.** MODIS (1 km pixels, since 2000) and VIIRS (375 m pixels, since 2012) see a very
different number of fires: since 2012 VIIRS reports several times more hotspots than MODIS for the
same area. If you simply count hotspots per year, every year after 2012 looks worse only because a
better satellite appeared. Early-warning services, scientists and land users therefore cannot tell a
truly unusual fire season from a change of sensor.

**What we built.** An interactive web app (Python, Streamlit, pandas, Plotly) that harmonizes both
sensors into one continuous series from 2001 to 2025 and turns it into a *fire-activity calendar*
for a chosen area of interest: all of Ukraine, any oblast, groups of oblasts, or custom coordinates.

**How we harmonize (4 steps).**
1. *Common schema.* Both FIRMS formats are mapped to the same fields; confidence is unified into
   low / nominal / high (MODIS: <30 low, ≥80 high; VIIRS: l/n/h). Only vegetation fires (`type = 0`).
2. *Common grid.* Points snap to a 0.1° grid (≈ 8×11 km). The unit is the **fire-cell-day**: a cell
   with at least one hotspot that day. This removes the pixel-size difference (1 km vs 375 m).
3. *Union.* Since VIIRS exists, a cell-day is active if MODIS **or** VIIRS saw fire there.
4. *Calibration.* In the overlap years we compute, for every calendar month, how much MODIS
   under-detects: k = union / MODIS. Before 2012 the series is MODIS × k[month]. k is always learned
   on all years of the selected area, so changing the year range never changes past values.

**What the user sees.**
- *Calendar*: year × week heat map. Repeating vertical bands are fire seasons; dark spots outside
  them are unusual years. Switching to "MODIS only" / "VIIRS only" shows why raw counts mislead.
- *Early warning*: the last N days before any date compared with the same days in other years —
  🟢 normal, 🟡 above 75% of years, 🟠 above 90%, 🔴 far outside normal (robust z ≥ 2).
  Example: Polissia, 10 April 2020 (Chernobyl Exclusion Zone fires) → 🔴.
- *Critical periods*: the weeks that carry most of the yearly burning
  (for Ukraine: March–April and July–September).
- *Anomalies*: unusually hot weeks with "how many times above normal".
- *Map*: where fires happen most often, by season.
- *MODIS vs VIIRS*: the problem and our solution explained step by step.
- Downloads: Excel with a header that records all settings, and CSV for scripts.

**Findings for Ukraine (2000–2025).** About 40 thousand fire-cell-days per year on average;
1.6 million raw hotspots aligned into one series of about 1.0 million fire-cell-days;
two fire seasons — spring (March–April, grass burning) and late summer (July–September, after
harvest), peaking around week 31; the worst year was 2008 (≈ 2× a normal year). Since 24 February 2022 most hotspots in
the east and south come from the war, not from vegetation fires (67% of points after that date are
in seven frontline oblasts), so the app has a switch that excludes those oblasts after that date.
`TODO`: додати знахідки Вані й Назара (#3).

**Impact.** Services and land users get one honest answer to "is this fire season unusual?" across
25 years, instead of numbers that jump in 2012 because of a sensor change.

### Use of artificial intelligence
We used an AI coding assistant (Claude Code by Anthropic) to help write and review code, tests and
documentation. All methodological decisions (grid, fire-cell-day unit, union, monthly calibration,
anomaly rules) were made, checked and can be explained by the team; results were verified against
known events (e.g. the April 2020 Chernobyl fires). No AI-generated images or data are used: all
charts are produced by our code from NASA FIRMS data.
`TODO`: перевірити, чи форма просить уточнити ще щось (напр. які саме частини).

### NASA data used
- NASA FIRMS — MODIS Collection 6.1 active fire / hotspot archive, Ukraine, 2000–2025
  (https://firms.modaps.eosdis.nasa.gov/)
- NASA FIRMS — VIIRS S-NPP 375 m active fire archive, Ukraine, 2012–2025

### Space agency partner data and other references
- geoBoundaries — administrative boundaries of Ukraine's oblasts (https://www.geoboundaries.org/)
- OpenStreetMap contributors / CARTO — base map tiles
- Software: Python, Streamlit, pandas, NumPy, Plotly, openpyxl
`TODO`: якщо використовували дані партнерських агенцій (ESA, JAXA тощо) — дописати; зараз їх немає.

---

## Презентація: 7 слайдів (англійською)

1. **Title** — Fire Activity Calendar: harmonizing MODIS and VIIRS hotspots. Команда, челендж,
   скрін банера з додатку.
2. **The problem** — графік «Raw satellite points per year» з вкладки «MODIS vs VIIRS»: стрибок у 2012.
   Одне речення: *"More points after 2012 ≠ more fires — it's a better satellite."*
3. **Our method** — 4 кроки (grid → fire-cell-day → union → monthly k), схемою.
4. **The calendar** — скрін теплової карти «рік × тиждень»: два сезони, незвичні роки.
5. **Early warning** — скрін 🔴 для Полісся 10.04.2020 (Чорнобиль). *"Would have flagged it."*
6. **Findings for Ukraine** — сезони, 2008, вплив війни з 2022 (і перемикач), знахідки Вані й Назара.
7. **Impact & next steps** — для кого (служби раннього попередження, науковці, землекористувачі),
   посилання на демо й GitHub, що далі (інші країни, щоденне оновлення через FIRMS API).

## Відео до 30 секунд (якщо обираємо відео)

| Секунди | Що на екрані | Текст (англійською) |
|---|---|---|
| 0–5 | Вкладка «MODIS vs VIIRS», сирі точки | "After 2012 satellites see far more fires — but it's the sensor, not the fires." |
| 5–12 | Перемикач «Лише MODIS» → «Обидва, узгоджено» в календарі | "We harmonize MODIS and VIIRS into one 25-year fire calendar." |
| 12–20 | Календар України, наведення на темну клітинку | "Seasons, critical periods and unusual weeks — at a glance, for any oblast." |
| 20–27 | Кнопка «Чорнобиль, квітень 2020» → 🔴 | "And an early warning: is now worse than usual?" |
| 27–30 | Банер + посилання | "Fire Activity Calendar. NASA FIRMS, harmonized." |
