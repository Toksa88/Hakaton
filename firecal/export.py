"""Вивантаження таблиць для кнопок «Скачати»: CSV (для коду) і Excel з оформленням (для людей)."""
import io

import pandas as pd
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from firecal import analytics


def to_excel_csv(df: pd.DataFrame, index: bool = False) -> bytes:
    """Таблицю -> байти CSV для кнопки st.download_button.

    Чому не звичайний df.to_csv():
    - Excel з українськими (і більшістю європейських) налаштуваннями Windows розділяє колонки
      крапкою з комою, а дробову частину пише через кому. Звичайний CSV ("1,5" через кому
      між колонками) він відкриє однією колонкою. Тому sep=";" і decimal=",".
    - "utf-8-sig" додає на початок невидиму мітку (BOM), за якою Excel розуміє, що це UTF-8,
      і показує кирилицю, а не «кракозябри».
    Прочитати такий файл назад у pandas: pd.read_csv(f, sep=";", decimal=",").
    """
    return df.to_csv(index=index, sep=";", decimal=",").encode("utf-8-sig")


def calendar_for_export(mat: pd.DataFrame, freq: str, months: list[str]) -> pd.DataFrame:
    """Календар (рядки = роки, стовпці = номери тижнів/місяців) -> таблиця з людськими заголовками.

    freq — "week" або "month". Тижні підписуємо датою першого дня ("02.04"), бо номер тижня
    людині нічого не каже. Наші тижні рахуються від 1 січня, тож дата однакова в усі
    невисокосні роки; беремо 2001 рік як зразок (у високосний з березня зсув на 1 день — не страшно).
    """
    out = mat.round(1).rename_axis("рік")
    if freq == "month":
        out.columns = months
    else:
        out.columns = [analytics.week_start(2001, w).strftime("%d.%m") for w in mat.columns]
    return out


def calendar_summary(mat: pd.DataFrame, labels: list[str]) -> dict[str, str]:
    """Кілька підсумків по календарю для шапки Excel-файлу: що людина хоче знати до того, як дивитись цифри.

    mat — календар (рядки = роки, стовпці = тижні/місяці), labels — підписи стовпців для людей
    (дати тижнів або назви місяців), у тому ж порядку, що й mat.columns.
    """
    if mat.empty:
        return {}
    by_year = mat.sum(axis=1)            # axis=1: сумуємо ВЗДОВЖ рядка -> сума за кожен рік
    by_col = mat.mean(axis=0)            # axis=0: середнє ВЗДОВЖ стовпця -> «норма» кожного тижня/місяця
    peak = labels[list(mat.columns).index(by_col.idxmax())]  # idxmax — стовпець з найбільшим середнім
    return {
        "Разом за всі роки": f"{by_year.sum():,.0f}".replace(",", " "),
        "Найактивніший рік": f"{by_year.idxmax()} ({by_year.max():,.0f})".replace(",", " "),
        "Найтихіший рік": f"{by_year.idxmin()} ({by_year.min():,.0f})".replace(",", " "),
        "Пік сезону (в середньому)": str(peak),
    }


def to_excel(df: pd.DataFrame, index: bool = False, heatmap: bool = False, sheet: str = "Дані",
             title: str | None = None, info: dict[str, str] | None = None) -> bytes:
    """Таблицю -> байти файлу .xlsx з оформленням, щоб у Excel одразу було красиво.

    title, info — шапка над таблицею: заголовок і пари «параметр: значення» (зона, роки,
    налаштування, підсумки). Людина спершу бачить, ЩО це за цифри, а під ними — самі цифри.

    Що робимо з аркушем (openpyxl — бібліотека, що вміє писати й форматувати .xlsx):
    - шапка: заголовок великим жирним, назви параметрів жирні;
    - заголовки таблиці жирні на сірому тлі, закріплені (не зникають при прокрутці);
    - якщо index=True, перша колонка (роки) теж закріплена — видно рік при прокрутці вправо;
    - дробові числа показуємо з 1 знаком після коми (кому/крапку Excel бере з налаштувань Windows);
    - ширина колонок — за найдовшим значенням;
    - heatmap=True: клітинки з числами фарбуються від світло-жовтого до червоного,
      як теплова карта в додатку.
    """
    info = info or {}
    # Скільки рядків займе шапка: заголовок + параметри + порожній рядок-відступ (0, якщо шапки нема)
    top = (1 if title else 0) + len(info)
    top = top + 1 if top else 0
    head = top + 1  # номер рядка Excel (з 1), де стоять заголовки таблиці

    buf = io.BytesIO()  # «файл у пам'яті»: пишемо туди, а не на диск, і віддаємо кнопці байти
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # startrow — з якого рядка (рахуючи з 0) pandas почне писати таблицю; вище лишається місце під шапку
        df.to_excel(writer, sheet_name=sheet, index=index, startrow=top)
        ws = writer.sheets[sheet]  # аркуш, який pandas щойно заповнив

        # Шапка: ws.cell(row, column) — клітинка за номерами (з 1)
        r = 1
        if title:
            ws.cell(r, 1, title).font = Font(bold=True, size=14)
            r += 1
        for key, value in info.items():
            ws.cell(r, 1, key).font = Font(bold=True)
            ws.cell(r, 2, value)  # довгий текст сам «вилізе» праворуч на порожні клітинки — так і задумано
            r += 1

        # Заголовки таблиці: жирні, сіре тло, по центру
        for cell in ws[head]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="E7E6E6")
            cell.alignment = Alignment(horizontal="center")
        if index:
            for row in ws.iter_rows(min_row=head + 1, max_col=1):  # колонка з роками — теж жирна
                row[0].font = Font(bold=True)

        # freeze_panes = "B9": закріпити все вище рядка 9 і лівіше колонки B (шапка + заголовки таблиці)
        ws.freeze_panes = f"{'B' if index else 'A'}{head + 1}"

        # Формат дробових чисел і ширина колонок — лише за клітинками таблиці (довгий текст шапки
        # не повинен роздувати першу колонку)
        for col_cells in ws.iter_cols(min_row=head):
            width = 4
            for cell in col_cells:
                if isinstance(cell.value, float):
                    cell.number_format = "0.0"
                width = max(width, len(str(cell.value if cell.value is not None else "")))
            ws.column_dimensions[get_column_letter(col_cells[0].column)].width = min(width + 2, 40)
        # Перша колонка має вмістити й назви параметрів шапки («Мін. достовірність» тощо)
        if info:
            first = ws.column_dimensions["A"]
            first.width = max(first.width or 0, max(len(k) for k in info) + 2)

        # Теплова карта: умовне форматування на всю область з числами (без заголовків і колонки років)
        if heatmap and ws.max_row > head:
            first_col = 2 if index else 1
            rng = f"{get_column_letter(first_col)}{head + 1}:{get_column_letter(ws.max_column)}{ws.max_row}"
            # Три точки шкали: мінімум — світло-жовтий, середина між min і max — помаранчевий, максимум — червоний.
            # "percent" (а не "percentile"): шкала лінійна за значенням, як на тепловій карті в додатку.
            # Із "percentile" медіана була б майже 0 (багато тихих тижнів) і все стало б помаранчевим.
            ws.conditional_formatting.add(rng, ColorScaleRule(
                start_type="min", start_color="FFFFCC",
                mid_type="percent", mid_value=50, mid_color="FD8D3C",
                end_type="max", end_color="BD0026"))
    return buf.getvalue()
