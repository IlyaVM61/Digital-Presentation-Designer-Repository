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


# --- T-39: три варианта одним запуском -------------------------------------
#
# Три варианта рядом требуют трёх превью, а конвертация LibreOffice — самая
# дорогая операция пайплайна. Измерено 2026-09-30 на VK Tech: одна колода —
# 8,8 с, три колоды одним процессом — 16,6 с, три отдельных запуска — около
# 26 с. Холодный старт платится один раз, поэтому колоды передаются пачкой.


def _fake_soffice(monkeypatch: pytest.MonkeyPatch, calls: list[list[str]], skip: str = "") -> None:
    """Подменить LibreOffice: он «конвертирует» всё, что передано, кроме `skip`."""
    from dpd.render import renderer

    def run(args, **_kwargs):
        calls.append(list(args))
        target = Path(args[args.index("--outdir") + 1])
        for item in args:
            if item.endswith(".pptx") and Path(item).stem != skip:
                (target / f"{Path(item).stem}.pdf").write_bytes(b"%PDF-1.4")
        return renderer.subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(renderer, "soffice_path", lambda: Path("soffice"))
    monkeypatch.setattr(renderer.subprocess, "run", run)


def _decks(tmp_path: Path, *stems: str) -> list[Path]:
    paths = [tmp_path / f"{stem}.pptx" for stem in stems]
    for path in paths:
        path.write_bytes(b"pptx")
    return paths


def test_several_decks_convert_in_one_libreoffice_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dpd.render import convert_to_pdfs

    calls: list[list[str]] = []
    _fake_soffice(monkeypatch, calls)
    decks = _decks(tmp_path, "deck-A", "deck-B", "deck-C")

    pdfs = convert_to_pdfs(decks, tmp_path / "out")

    assert len(calls) == 1, f"LibreOffice запущен {len(calls)} раз"
    assert [pdf.stem for pdf in pdfs] == ["deck-A", "deck-B", "deck-C"], "порядок не сохранён"
    assert all(pdf.is_file() for pdf in pdfs)


def test_decks_with_one_name_are_refused_before_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PDF называется по имени колоды: одноимённые перезаписали бы друг друга.

    Молча выдать превью одного варианта за превью другого — ровно та ошибка,
    ради которой варианты показываются рядом.
    """
    from dpd.render import convert_to_pdfs

    calls: list[list[str]] = []
    _fake_soffice(monkeypatch, calls)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    decks = [*_decks(tmp_path / "a", "deck"), *_decks(tmp_path / "b", "deck")]

    with pytest.raises(ValueError, match="deck"):
        convert_to_pdfs(decks, tmp_path / "out")
    assert not calls, "LibreOffice запущен зря"


def test_missing_pdf_names_the_deck(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Если LibreOffice не справился с одной колодой, видно — с какой."""
    from dpd.render import convert_to_pdfs

    _fake_soffice(monkeypatch, [], skip="deck-B")

    with pytest.raises(RuntimeError, match="deck-B"):
        convert_to_pdfs(_decks(tmp_path, "deck-A", "deck-B"), tmp_path / "out")
