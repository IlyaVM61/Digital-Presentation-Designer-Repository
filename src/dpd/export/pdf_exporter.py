"""Экспорт колоды в PDF (T-36).

PDF — один из трёх форматов, которые ТЗ требует обязательно.

**Делается из выгруженного `.pptx`, а не из контракта вёрстки.** Так PDF
показывает ровно то, что получит пользователь, открыв колоду в PowerPoint:
шрифты, подставленные шаблоном, применённые макеты, нативные таблицы и
диаграммы. Сборка PDF отдельным путём по тем же данным дала бы второй
рендерер и, значит, второй набор расхождений.

**Число страниц сверяется с числом слайдов.** PDF, в котором страниц меньше,
открывается без ошибок и выглядит целым — пропажу заметит только тот, кто
станет считать. Поэтому расхождение останавливает выгрузку: тихо отдать файл
с потерянным слайдом хуже, чем не отдать ничего.

**Готовый PDF используется повторно.** Конвертация LibreOffice — самая дорогая
операция пайплайна, 13–28 с на колоду, и в полном прогоне она уже выполнена
ради превью и проверок класса `rendered`. Второй запуск потратил бы столько
же времени на тот же результат, а бюджет ТЗ — пять минут на всё.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pymupdf
from pptx import Presentation

from dpd.render import DEFAULT_TIMEOUT_SEC, convert_to_pdf


def export_pdf(
    pptx_path: str | Path,
    output_path: str | Path,
    *,
    source_pdf: str | Path | None = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> Path:
    """Выгрузить колоду в PDF и убедиться, что не потерялось ни одного слайда.

    `source_pdf` — уже готовый PDF этой же колоды, например сделанный шагом
    рендера. Тогда LibreOffice не запускается, а файл переносится как есть;
    сверка числа страниц выполняется в любом случае.
    """
    pptx_path = Path(pptx_path)
    if not pptx_path.is_file():
        raise FileNotFoundError(f"файл не найден: {pptx_path}")

    output_path = Path(output_path)
    if output_path.suffix.lower() != ".pdf":
        output_path = output_path.with_suffix(".pdf")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    produced = (
        Path(source_pdf)
        if source_pdf is not None
        else convert_to_pdf(pptx_path, output_path.parent, timeout_sec=timeout_sec)
    )
    if not produced.is_file():
        raise FileNotFoundError(f"PDF не найден: {produced}")

    if produced.resolve() != output_path.resolve():
        shutil.copyfile(produced, output_path)

    _verify(pptx_path, output_path)
    return output_path


def _verify(pptx_path: Path, pdf_path: Path) -> None:
    """Сверить число страниц с числом слайдов.

    Проверка идёт по готовому файлу, а не по журналу конвертации: открылся ли
    PDF и сколько в нём страниц — единственное, что имеет значение для того,
    кто его получит.
    """
    slides = len(Presentation(str(pptx_path)).slides)
    with pymupdf.open(pdf_path) as document:
        pages = document.page_count

    if pages != slides:
        raise ValueError(
            f"в PDF {pages} страниц, а в колоде {slides} слайдов: "
            f"при конвертации потерялось содержимое, файл {pdf_path.name} отдавать нельзя"
        )
