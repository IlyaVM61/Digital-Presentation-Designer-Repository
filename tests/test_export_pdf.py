"""T-36: экспорт колоды в PDF.

Критерий приёмки: **PDF открывается, число страниц равно числу слайдов**.

Оба условия проверяет сам экспортёр, а не только тест. PDF, в котором
страниц меньше, чем слайдов, открывается без ошибок и выглядит целым —
пропажу заметит только тот, кто станет считать. Поэтому сверка идёт внутри
экспорта, и расхождение останавливает выгрузку.

PDF — один из трёх форматов, которые ТЗ требует обязательно. Делается он из
уже выгруженного `.pptx`, а не из контракта вёрстки: так PDF показывает ровно
то, что получит пользователь, открыв колоду в PowerPoint.

Конвертация — самая дорогая операция пайплайна: 13–28 с на колоду. Поэтому
экспорт умеет взять готовый PDF, если его уже сделал шаг рендера, и не
запускать LibreOffice второй раз.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest

from dpd.export import export_pdf, export_pptx
from dpd.layout import compose_variants
from dpd.models import PresentationStructure, SlideBody, StructureSlide
from dpd.parsing import parse_template
from dpd.render import soffice_path

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"
TEMPLATE = "VK Tech шаблон.pptx"


@pytest.fixture(scope="module")
def out_dir() -> Iterator[Path]:
    base = Path(os.environ.get("DPD_OUTPUT_DIR", "D:/dpd-out"))
    path = base / "tests" / "pdf"
    path.mkdir(parents=True, exist_ok=True)
    yield path


def structure(slides: int = 3) -> PresentationStructure:
    roles = ["title", "data", "process"]
    return PresentationStructure(
        slides=[
            StructureSlide(
                id=f"s{number}",
                role=roles[(number - 1) % len(roles)],
                headline=f"Слайд {number}",
                body=None if number == 1 else SlideBody(items=["Первый пункт", "Второй пункт"]),
            )
            for number in range(1, slides + 1)
        ]
    )


@pytest.fixture(scope="module")
def deck(out_dir: Path) -> Path:
    """Колода, выгруженная из живого шаблона: из неё и делается PDF."""
    template = CALIBRATION / TEMPLATE
    if not template.exists():
        pytest.skip(f"нет калибровочного шаблона {TEMPLATE}")

    schema = parse_template(template)
    rendered = compose_variants(structure(), schema)[0]
    return export_pptx(rendered, schema, template, out_dir / "deck.pptx")


def pdf_with(path: Path, pages: int) -> Path:
    """PDF заданного числа страниц — подставляется вместо конвертации."""
    document = pymupdf.open()
    for _ in range(pages):
        document.new_page()
    document.save(str(path))
    document.close()
    return path


# --- Критерий приёмки -----------------------------------------------------


def test_pdf_opens_and_has_a_page_per_slide(deck: Path, out_dir: Path) -> None:
    """Обе половины критерия на живой колоде.

    Тест интеграционный и медленный: конвертация занимает десятки секунд —
    это самая дорогая операция пайплайна.
    """
    if soffice_path() is None:
        pytest.skip("LibreOffice не найден")

    result = export_pdf(deck, out_dir / "deck.pdf")

    assert result.is_file() and result.suffix == ".pdf"
    with pymupdf.open(result) as document:
        assert document.page_count == 3, "страниц в PDF не столько же, сколько слайдов"
        assert document[0].get_text().strip(), "первая страница пуста"


def test_page_count_mismatch_stops_the_export(deck: Path, tmp_path: Path) -> None:
    """Потеря слайда при конвертации не проходит молча.

    PDF с недостающей страницей открывается и выглядит целым: заметит только
    тот, кто станет считать. Выгрузка такого файла — тихая потеря работы.
    """
    wrong = pdf_with(tmp_path / "wrong.pdf", pages=1)

    with pytest.raises(ValueError, match="страниц"):
        export_pdf(deck, tmp_path / "deck.pdf", source_pdf=wrong)


# --- Повторное использование конвертации -----------------------------------


def test_ready_pdf_is_reused_instead_of_converting_again(deck: Path, tmp_path: Path) -> None:
    """Готовый PDF от шага рендера берётся как есть, без второй конвертации.

    Конвертация LibreOffice — 13–28 с на колоду, и в полном прогоне она уже
    выполнена ради превью. Второй запуск потратил бы столько же времени на
    тот же результат, а бюджет ТЗ — пять минут на всё.

    Подставленный PDF собран здесь же: совпадение байтов доказывает, что
    LibreOffice не запускался — его вывод выглядел бы иначе.
    """
    ready = pdf_with(tmp_path / "ready.pdf", pages=3)

    result = export_pdf(deck, tmp_path / "copy.pdf", source_pdf=ready)

    assert result.read_bytes() == ready.read_bytes()


# --- Обращение с путями и ошибками -----------------------------------------


def test_missing_deck_is_a_loud_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        export_pdf(tmp_path / "нет-такой.pptx", tmp_path / "out.pdf")


def test_output_directory_is_created(deck: Path, tmp_path: Path) -> None:
    """Каталог назначения создаётся: результаты прогонов лежат отдельно от кода."""
    ready = pdf_with(tmp_path / "ready.pdf", pages=3)
    target = tmp_path / "глубоко" / "вложенный" / "deck.pdf"

    result = export_pdf(deck, target, source_pdf=ready)

    assert result == target and result.is_file()


def test_extension_is_added_when_missing(deck: Path, tmp_path: Path) -> None:
    """Имя без расширения получает `.pdf`: файл открывается двойным щелчком."""
    ready = pdf_with(tmp_path / "ready.pdf", pages=3)

    result = export_pdf(deck, tmp_path / "колода", source_pdf=ready)

    assert result.name == "колода.pdf"
