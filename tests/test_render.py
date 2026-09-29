"""T-09: рендер слайда в изображение через LibreOffice.

Критерий приёмки задачи: из `.pptx` получается PNG, изображение непустое,
размер соответствует холсту.

Путь рендера состоит из двух шагов: LibreOffice конвертирует `.pptx` в PDF,
затем `pymupdf` рендерит страницы в PNG. Второй шаг понадобился потому, что
`pdftoppm` в системе отсутствует.

Тест интеграционный и медленный: холодный старт LibreOffice занимает
секунды. Рендер выполняется один раз на модуль.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from dpd.export import export_pptx
from dpd.layout import compose
from dpd.models import PresentationStructure, SlideBody, StructureSlide
from dpd.parsing import parse_template
from dpd.render import convert_to_pdf, render_slides, soffice_path

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"
TEMPLATE = "VK Tech шаблон.pptx"


@pytest.fixture(scope="module")
def out_dir() -> Iterator[Path]:
    base = Path(os.environ.get("DPD_OUTPUT_DIR", "D:/dpd-out"))
    path = base / "tests" / "render"
    path.mkdir(parents=True, exist_ok=True)
    yield path


@pytest.fixture(scope="module")
def deck(out_dir: Path) -> Path:
    """Колода из двух слайдов, собранная нашим же пайплайном."""
    template_path = CALIBRATION / TEMPLATE
    if not template_path.exists():
        pytest.skip(f"нет калибровочного шаблона {TEMPLATE}")
    if soffice_path() is None:
        pytest.skip("LibreOffice не найден: задайте SOFFICE_PATH")

    structure = PresentationStructure(
        slides=[
            StructureSlide(id="s1", role="title", headline="Программа наставничества"),
            StructureSlide(
                id="s2",
                role="data",
                headline="Пилот окупился за четыре квартала",
                body=SlideBody(kind="bullets", items=["Экономия 9,4 млн", "Затраты 2,8 млн"]),
            ),
        ]
    )
    template = parse_template(template_path)
    return export_pptx(compose(structure, template), template, template_path, out_dir / "deck.pptx")


@pytest.fixture(scope="module")
def images(deck: Path, out_dir: Path) -> list[Path]:
    return render_slides(deck, out_dir / "png")


def test_pdf_is_produced_with_one_page_per_slide(deck: Path, out_dir: Path) -> None:
    import pymupdf

    pdf = convert_to_pdf(deck, out_dir / "pdf")
    assert pdf.is_file()
    with pymupdf.open(str(pdf)) as document:
        assert document.page_count == 2


def test_one_image_per_slide(images: list[Path]) -> None:
    assert len(images) == 2


def test_images_exist_and_are_not_empty_files(images: list[Path]) -> None:
    for image in images:
        assert image.is_file()
        assert image.stat().st_size > 0


def test_images_are_not_blank(images: list[Path]) -> None:
    """Непустое изображение: на слайде есть хоть что-то, кроме одного цвета.

    Проверка небесполезна — рендер, отдавший равномерную заливку, означал бы,
    что LibreOffice не применил оформление или не нашёл содержимого.
    """
    for image in images:
        with Image.open(image) as rendered:
            colours = rendered.convert("RGB").getcolors(maxcolors=1 << 24)
        assert colours is not None
        assert len(colours) > 1, f"{image.name} залит одним цветом"


def test_image_proportions_match_the_canvas(images: list[Path]) -> None:
    """Размер соответствует холсту шаблона, а не бумажному формату.

    LibreOffice умеет подставить A4, если не разобрал размер слайда; тогда
    пропорции разойдутся, и все проверки класса `rendered` поедут вместе с
    ними.
    """
    template = parse_template(CALIBRATION / TEMPLATE)
    expected = template.canvas.width_emu / template.canvas.height_emu
    for image in images:
        with Image.open(image) as rendered:
            actual = rendered.width / rendered.height
        assert abs(actual - expected) < 0.02, f"{image.name}: {actual:.3f} против {expected:.3f}"


def test_dpi_controls_resolution(deck: Path, out_dir: Path) -> None:
    small = render_slides(deck, out_dir / "png-72", dpi=72)
    with Image.open(small[0]) as rendered:
        low = rendered.width
    with Image.open(render_slides(deck, out_dir / "png-144", dpi=144)[0]) as rendered:
        high = rendered.width
    assert high > low


def test_missing_file_raises_a_clear_error(out_dir: Path) -> None:
    with pytest.raises(FileNotFoundError):
        convert_to_pdf(out_dir / "нет-такой.pptx", out_dir)
