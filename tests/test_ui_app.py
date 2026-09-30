"""T-38: интерфейс Streamlit — загрузка, диагностика, план, прогресс.

Критерий приёмки: **сценарий проходится от файла до экспорта**. Здесь он и
проходится — не описанием, а прогоном страницы через `AppTest`: загрузка
шаблона, диагностика разбора, ввод плана, сборка, готовые файлы.

**Диагностика показывается до сборки.** Шаблон может оказаться непригодным —
у него бывает один макет, размеченный одним заголовком, или тема, врущая про
гарнитуру. Узнать об этом до того, как ждать полминуты, честнее, чем после.

Интерфейс сам ничего не считает: он вызывает оркестратор и показывает его
события. Логика пайплайна в странице означала бы вторую реализацию рядом с
первой.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "ui" / "app.py"
CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"
TEMPLATE = "VK Tech шаблон.pptx"

OUTLINE = """Итоги пилота
Что изменилось за квартал
- Срок сборки колоды сократился с девяти дней до трёх
- Правки текста переживают сохранение файла
"""


@pytest.fixture
def out_dir(tmp_path: Path) -> Iterator[Path]:
    base = Path(os.environ.get("DPD_OUTPUT_DIR", "D:/dpd-out")) / "tests" / "ui"
    base.mkdir(parents=True, exist_ok=True)
    os.environ["DPD_OUTPUT_DIR"] = str(base)
    yield base


@pytest.fixture
def app(out_dir: Path) -> AppTest:
    return AppTest.from_file(str(APP), default_timeout=180)


def template_file() -> Path:
    path = CALIBRATION / TEMPLATE
    if not path.exists():
        pytest.skip(f"нет калибровочного шаблона {TEMPLATE}")
    return path


def upload(app: AppTest, path: Path) -> AppTest:
    """Загрузить шаблон так же, как это сделает пользователь в браузере."""
    app.run()
    app.file_uploader[0].upload(
        path.name,
        path.read_bytes(),
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    return app.run()


# --- Страница открывается --------------------------------------------------


def test_page_offers_upload_and_plan(app: AppTest) -> None:
    """До загрузки шаблона страница объясняет, чего ждёт."""
    app.run()

    assert not app.exception
    assert app.title[0].value.startswith("Цифровой дизайнер")
    assert app.file_uploader, "нет поля загрузки шаблона"
    assert app.text_area, "нет поля плана колоды"


def test_build_button_without_a_template_does_not_crash(app: AppTest) -> None:
    """Кнопка без шаблона объясняет, чего не хватает, а не падает."""
    app.run()
    app.button[0].click().run()

    assert not app.exception
    assert app.warning, "интерфейс промолчал вместо объяснения"


# --- Диагностика шаблона ---------------------------------------------------


def test_diagnostics_appear_right_after_upload(app: AppTest) -> None:
    """Разбор показывается до сборки: непригодный шаблон видно сразу.

    Ждать полминуты, чтобы узнать, что у шаблона один макет с одним
    заголовком, — впустую потраченное время пользователя.
    """
    upload(app, template_file())

    assert not app.exception
    diagnostics = " ".join(item.value for item in app.markdown)
    assert "макет" in diagnostics.lower(), "число макетов не показано"
    assert "Play" in diagnostics, "ведущая гарнитура шаблона не показана"


# --- Сценарий от файла до экспорта -----------------------------------------


def test_scenario_runs_from_file_to_exported_files(app: AppTest) -> None:
    """Критерий приёмки задачи целиком.

    Берутся `.pptx` и `.html`: оба быстрые. PDF требует LibreOffice и
    десятков секунд — он проверен в тестах оркестратора и экспорта.
    """
    upload(app, template_file())
    app.text_area[0].set_value(OUTLINE)
    for checkbox in app.checkbox:
        нужен = any(fmt in checkbox.label.lower() for fmt in ("pptx", "html"))
        checkbox.set_value(нужен)
    app.button[0].click().run()

    assert not app.exception, app.exception

    итог = " ".join(item.value for item in app.markdown) + " ".join(item.value for item in app.success)
    assert "готов" in итог.lower() or app.download_button, "интерфейс не сообщил о результате"
    assert len(app.download_button) >= 2, "файлы не предложены к скачиванию"

    имена = {button.label for button in app.download_button}
    assert any("pptx" in name.lower() for name in имена)
    assert any("html" in name.lower() for name in имена)


def test_timings_are_shown_after_the_run(app: AppTest) -> None:
    """Время этапов видно пользователю: бюджет ТЗ — пять минут на цикл."""
    upload(app, template_file())
    app.text_area[0].set_value(OUTLINE)
    app.button[0].click().run()

    текст = " ".join(item.value for item in app.markdown)
    assert "с" in текст and any(stage in текст for stage in ("Разбор шаблона", "Выгрузка"))


def test_empty_plan_is_refused_with_an_explanation(app: AppTest) -> None:
    """Пустой план — не колода из нуля слайдов, а несделанная работа."""
    upload(app, template_file())
    app.text_area[0].set_value("   ")
    app.button[0].click().run()

    assert not app.exception
    assert app.warning or app.error, "интерфейс не объяснил, чего не хватает"
