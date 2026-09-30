"""T-42: девять колод промежуточной сдачи одной командой.

Матрица — три калибровочных шаблона на три варианта вёрстки, на одном
контенте (`docs/08-backlog/deliverables.md`). Имена файлов заданы схемой
сдачи: `{слаг шаблона}__{вариант}`, колоды в `decks/`, отчёты аудита в
`audit/`. По имени комиссия находит колоду, не открывая её, и схема
проверяется здесь буквально.

Основная команда гоняется один раз на модуль и без PDF: конвертация
LibreOffice — 16 с на шаблон, а имена от формата не зависят. PDF проверяется
отдельно, на одном шаблоне.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from dpd.batch import deck_name, load_matrix, main
from dpd.render import soffice_path

OUTLINE = """Итоги пилота
Что изменилось за квартал
- Срок сборки колоды сократился с девяти дней до трёх
- Правки текста переживают сохранение файла
Как шли
- Разбор шаблона
- Вёрстка
- Аудит
"""

SLUGS = ("vk-tech", "vk-workspace", "vk-education")
NINE = [f"{slug}__{variant}" for slug in SLUGS for variant in "abc"]


def _base() -> Path:
    return Path(os.environ.get("DPD_OUTPUT_DIR", "D:/dpd-out")) / "tests" / "batch"


def _fresh(name: str) -> Path:
    path = _base() / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


@pytest.fixture(scope="module")
def outline() -> Path:
    path = _base() / "outline.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(OUTLINE, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def built(outline: Path) -> Iterator[tuple[int, Path]]:
    for _, template in load_matrix():
        if not template.exists():
            pytest.skip(f"нет калибровочного шаблона {template.name}")
    out = _fresh("nine")
    code = main(["--outline", str(outline), "--out", str(out), "--formats", "pptx,html"])
    yield code, out


# --- Матрица и имена -------------------------------------------------------


def test_matrix_names_the_three_calibration_templates() -> None:
    matrix = load_matrix()
    assert [slug for slug, _ in matrix] == list(SLUGS)
    assert all(template.suffix == ".pptx" for _, template in matrix)


def test_deck_name_follows_the_deliverables_scheme() -> None:
    assert deck_name("vk-tech", "A") == "vk-tech__a"
    assert deck_name("vk-education", "c") == "vk-education__c"


# --- Одна команда — девять колод -------------------------------------------


def test_one_command_produces_nine_decks(built: tuple[int, Path]) -> None:
    code, out = built
    assert code == 0
    for extension in ("pptx", "html"):
        names = sorted(path.stem for path in (out / "decks").glob(f"*.{extension}"))
        assert names == sorted(NINE), extension


def test_every_deck_has_its_audit_report(built: tuple[int, Path]) -> None:
    _, out = built
    reports = sorted((out / "audit").glob("*.report.json"))
    assert [path.name.removesuffix(".report.json") for path in reports] == sorted(NINE)
    for path in reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        # Отчёт принадлежит своей колоде, а не соседней: вариант в имени
        # файла и внутри отчёта совпадает.
        assert path.name.split("__")[1][0] == report["variant"].lower()
        assert report["timings"], path.name


def test_nothing_but_the_scheme_is_left_behind(built: tuple[int, Path]) -> None:
    _, out = built
    # Рабочие файлы прогона — промежуточные PDF, имена по файлу шаблона — в
    # каталог сдачи не попадают: комиссия увидела бы в нём лишнее.
    assert sorted(path.name for path in out.iterdir()) == ["audit", "decks"]


# --- Форматы ---------------------------------------------------------------


def test_every_deck_goes_out_in_three_formats(outline: Path) -> None:
    if soffice_path() is None:
        pytest.skip("LibreOffice не найден")
    matrix = dict(load_matrix())
    if not matrix["vk-tech"].exists():
        pytest.skip("нет калибровочного шаблона VK Tech")
    config = _base() / "one.yaml"
    config.write_text(
        f"version: 1\ntemplates:\n  - slug: vk-tech\n    file: {matrix['vk-tech'].as_posix()}\n",
        encoding="utf-8",
    )
    out = _fresh("formats")

    code = main(["--outline", str(outline), "--out", str(out), "--matrix", str(config)])

    assert code == 0
    files = sorted(path.name for path in (out / "decks").iterdir())
    assert files == sorted(
        f"vk-tech__{variant}.{extension}" for variant in "abc" for extension in ("pptx", "pdf", "html")
    )


# --- Отказы ----------------------------------------------------------------


def test_broken_template_does_not_stop_the_others(outline: Path) -> None:
    matrix = dict(load_matrix())
    if not matrix["vk-tech"].exists():
        pytest.skip("нет калибровочного шаблона VK Tech")
    broken = _base() / "broken.pptx"
    broken.write_bytes(b"not a presentation")
    config = _base() / "broken.yaml"
    config.write_text(
        "version: 1\ntemplates:\n"
        f"  - slug: broken\n    file: {broken.as_posix()}\n"
        f"  - slug: vk-tech\n    file: {matrix['vk-tech'].as_posix()}\n",
        encoding="utf-8",
    )
    out = _fresh("broken")

    code = main(["--outline", str(outline), "--out", str(out), "--matrix", str(config), "--formats", "html"])

    # Сбой одного шаблона виден по коду возврата, но остальные колоды
    # собраны: девять колод не должны зависеть от самого слабого файла.
    assert code != 0
    assert sorted(path.stem for path in (out / "decks").iterdir()) == ["vk-tech__a", "vk-tech__b", "vk-tech__c"]


def test_unknown_format_is_refused(outline: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--outline", str(outline), "--out", str(tmp_path), "--formats", "pptx,docx"])


def test_outline_is_required(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--out", str(tmp_path)])
