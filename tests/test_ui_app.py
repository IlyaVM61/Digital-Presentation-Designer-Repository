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


def test_results_survive_a_download_click(app: AppTest) -> None:
    """Скачивание одного файла не уносит со страницы остальные.

    Нажатие «Скачать» перезапускает страницу — в Streamlit это такое же
    действие, как любое другое. Пока итог жил внутри обработчика кнопки
    «Собрать», после первого же скачивания исчезали и остальные кнопки, и
    отчёт аудита: пользователь получал один файл из трёх и пустую страницу.
    """
    upload(app, template_file())
    app.text_area[0].set_value(OUTLINE)
    for checkbox in app.checkbox:
        checkbox.set_value(any(fmt in checkbox.label.lower() for fmt in ("pptx", "html")))
    app.button[0].click().run()

    было = len(app.download_button)
    assert было >= 2

    app.download_button[0].click().run()

    assert not app.exception
    assert len(app.download_button) == было, "после скачивания кнопки пропали"
    assert app.success, "отчёт о прогоне исчез со страницы"


def test_theme_font_conflict_is_shown(app: AppTest) -> None:
    """Расхождение темы с разметкой видно пользователю.

    Тема файла врёт во всех четырёх проверенных шаблонах: объявляет Arial
    при фактическом Play. На этом основано решение системы поверить
    разметке, и основание надо показывать, а не прятать.
    """
    upload(app, template_file())

    диагностика = " ".join(item.value for item in app.markdown)
    assert "Play" in диагностика
    assert "Arial" in диагностика, "объявление темы не показано"


def test_timings_are_shown_after_the_run(app: AppTest) -> None:
    """Время этапов видно пользователю: бюджет ТЗ — пять минут на цикл."""
    from dpd.orchestrator import STAGE_TITLES

    upload(app, template_file())
    app.text_area[0].set_value(OUTLINE)
    app.button[0].click().run()

    текст = " ".join(item.value for item in app.markdown)
    assert "с" in текст and any(stage in текст for stage in STAGE_TITLES.values())


def test_empty_plan_is_refused_with_an_explanation(app: AppTest) -> None:
    """Пустой план — не колода из нуля слайдов, а несделанная работа."""
    upload(app, template_file())
    app.text_area[0].set_value("   ")
    app.button[0].click().run()

    assert not app.exception
    assert app.warning or app.error, "интерфейс не объяснил, чего не хватает"


# --- T-39: три варианта рядом и выбор -------------------------------------
#
# Критерий приёмки: различие видно без пояснений. Значит, варианты стоят
# рядом — строка на слайд, колонка на вариант, — а не за переключателем, и
# выбирает пользователь после того, как посмотрел, а не до прогона.


def build(app: AppTest, *, formats: tuple[str, ...], previews: bool) -> AppTest:
    """Собрать колоду с нужными галочками."""
    upload(app, template_file())
    app.text_area[0].set_value(OUTLINE)
    for checkbox in app.checkbox:
        if checkbox.label.startswith("Превью"):
            checkbox.set_value(previews)
        else:
            checkbox.set_value(any(fmt in checkbox.label.lower() for fmt in formats))
    return app.button[0].click().run()


def test_three_variants_are_shown_side_by_side(app: AppTest) -> None:
    from dpd.layout.variants import load_profiles
    from dpd.render import soffice_path

    if soffice_path() is None:
        pytest.skip("LibreOffice не найден")

    build(app, formats=("pptx",), previews=True)

    assert not app.exception, app.exception
    result = app.session_state["result"]
    картинки = [url for node in app.get("image") for url in node.value]
    assert len(картинки) == 3 * len(result.chosen.slides), "не каждый слайд показан в трёх вариантах"

    текст = " ".join(item.value for item in app.markdown)
    for profile in load_profiles():
        assert profile.name in текст, f"вариант «{profile.name}» не подписан"


def test_previews_are_on_by_default_when_libreoffice_is_there(app: AppTest) -> None:
    """Сравнение глазами — основной путь сценария, а не опция для знающих."""
    from dpd.render import soffice_path

    app.run()
    превью = next(box for box in app.checkbox if box.label.startswith("Превью"))

    assert превью.value is (soffice_path() is not None)


def test_choosing_a_variant_switches_the_files(app: AppTest) -> None:
    """Выбор после прогона ничего не пересобирает: файлы всех трёх готовы."""
    build(app, formats=("pptx", "html"), previews=False)

    assert not app.exception, app.exception
    assert app.radio, "выбрать вариант нечем"
    выбор = app.radio[0]
    assert len(выбор.options) == 3

    выбор.set_value("C").run()

    assert not app.exception, app.exception
    имена = [button.label for button in app.download_button]
    assert len(имена) == 2
    assert all("-C." in name for name in имена), f"предложены файлы не того варианта: {имена}"


def test_identical_variants_are_reported_on_the_page(
    app: AppTest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Три одинаковые колонки без объяснения — худшее, что можно показать."""
    import yaml

    same = {"prefer_scheme": "any", "prefer_families": ["content"], "size_shift": 0}
    config = tmp_path / "variants.yaml"
    config.write_text(
        yaml.safe_dump(
            {"profiles": [{"id": key, "name": f"Вариант {key}", **same} for key in "ABC"]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DPD_VARIANTS_CONFIG", str(config))

    build(app, formats=("html",), previews=False)

    assert not app.exception, app.exception
    предупреждения = " ".join(item.value for item in app.warning)
    assert "вариант" in предупреждения.lower(), "совпадение вариантов не показано"


# --- T-55: язык интерфейса для непрофильного пользователя ------------------
#
# Критерий приёмки: маркетолог проходит сценарий, не встречая жаргона вёрстки.
# Проверяется не на глаз: сценарий проходится страницей, собирается текст,
# видный без раскрытия подробностей, и сверяется со словарём ниже.
#
# Подробности при этом не удаляются, а сворачиваются: дизайнеру они нужны, и
# на них держится правило владельца «назвать свой выбор» (ADR-0006).

JARGON = {
    "слот": r"\bслот",
    "кегль": r"\bкегл",
    "гарнитура": r"\bгарнитур",
    "разметка": r"\bразметк",
    "пункты (pt)": r"\bpt\b",
    "мастер-слайд": r"\bмастер",
    "наследование стилей": r"\bнаследова",
    "токен": r"\bтокен",
    "плейсхолдер": r"\bплейсхолдер",
    "оси вариантов": r"\bос(?:ь|и|ей|ям|ях)\b",
    "цветовая схема макета": r"\bцветов\w* схем",
    "аудит": r"\bаудит",
    "находка": r"\bнаходк",
    "рендер": r"\bрендер",
    "колода": r"\bколод",
    "прогон": r"\bпрогон",
    "уверенность": r"\bуверенност",
    "стратегия разбора": r"\bстратеги",
    "выгрузка": r"\bвыгрузк",
    "путь к конфигу": r"configs[/\]|\.yaml\b",
    "внутренний идентификатор": r"\b(?:placeholder|geometry|examples)-first\b|\bconfidence\b|\bemu\b",
}
"""Слова, которые маркетолог встречать не должен. «Макет» сюда не входит:
так называет макеты слайдов сам PowerPoint."""

TEXT_ELEMENTS = {"markdown", "caption", "title", "header", "subheader", "warning", "info", "success", "error"}


def page_text(app: AppTest, *, folded: bool) -> str:
    """Текст страницы: видный сразу (`folded=False`) или свёрнутый.

    Подпись свёрнутого блока видна, его содержимое — нет. Поле плана не
    читается вовсе: там текст самого пользователя, а не страницы.
    """
    from streamlit.testing.v1.element_tree import Block

    части: list[str] = []

    def обойти(node, внутри: bool) -> None:
        if isinstance(node, Block):
            if node.type == "expander":
                if not внутри and not folded:
                    части.append(node.label)
                внутри = True
            for child in node.children.values():
                обойти(child, внутри)
            return
        if внутри is not folded:
            return
        if node.type in TEXT_ELEMENTS:
            части.append(node.value)
        elif node.type == "progress":
            части.append(node.proto.text)
        elif node.type == "radio":
            части.extend(node.options)
            части.extend(node.proto.captions)
        for attr in ("label", "help"):
            value = getattr(node, attr, None)
            if isinstance(value, str):
                части.append(value)

    обойти(app.main, False)
    return "\n".join(части)


def jargon_in(text: str) -> list[str]:
    """Найденный жаргон — с окружением, чтобы было видно, где он."""
    import re

    найдено = []
    for name, pattern in JARGON.items():
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            начало = max(match.start() - 40, 0)
            найдено.append(f"{name}: «…{text[начало:match.end() + 40]}…»")
    return найдено


def test_plain_view_has_no_layout_jargon(app: AppTest) -> None:
    """Критерий приёмки целиком: сценарий от шаблона до скачивания.

    На виду остаются диагностика шаблона, итог сборки, выбор варианта и
    файлы. Всё это должно читаться без знания вёрстки.
    """
    build(app, formats=("pptx", "html"), previews=False)
    app.radio[0].set_value("C").run()

    assert not app.exception, app.exception
    assert not jargon_in(page_text(app, folded=False))


def test_technical_details_are_folded_not_lost(app: AppTest) -> None:
    """Подробности для дизайнера остаются на странице, только свёрнутыми.

    Стратегия разбора, шкала размеров и объявление темы — основание
    решений системы. Спрятать их совсем значило бы решать молча.
    """
    from typing import get_args

    from dpd.models.template import ParsingStrategy

    upload(app, template_file())

    assert not app.exception, app.exception
    assert app.expander, "подробностей нет вовсе"
    свёрнуто = page_text(app, folded=True)
    assert any(strategy in свёрнуто for strategy in get_args(ParsingStrategy)), "стратегия разбора пропала"
    assert "pt" in свёрнуто, "шкала размеров пропала"
    assert "Arial" in page_text(app, folded=False) + свёрнуто, "объявление темы пропало"


def test_similar_variants_are_explained_in_plain_words(
    app: AppTest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Живой пример жаргона от владельца: «Работает осей: 2 из 3».

    Предупреждение о похожих вариантах говорит, чем они не различаются,
    словами пользователя. Техническая формулировка проверки остаётся
    в подробностях.
    """
    import yaml

    same = {"prefer_scheme": "any", "prefer_families": ["content"], "size_shift": 0}
    config = tmp_path / "variants.yaml"
    config.write_text(
        yaml.safe_dump(
            {"profiles": [{"id": key, "name": f"Вариант {key}", **same} for key in "ABC"]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DPD_VARIANTS_CONFIG", str(config))

    build(app, formats=("html",), previews=False)

    assert not app.exception, app.exception
    assert app.warning, "совпадение вариантов не показано"
    assert not jargon_in(page_text(app, folded=False))
    assert "осей" in page_text(app, folded=True), "формулировка проверки пропала из подробностей"


def test_broken_file_is_explained_not_crashed(app: AppTest) -> None:
    """Не тот файл — фраза о том, что делать, а не трассировка Python."""
    app.run()
    app.file_uploader[0].upload("презентация.pptx", b"not a presentation", "application/octet-stream")
    app.run()

    assert not app.exception, "вместо объяснения — трассировка"
    assert app.error, "интерфейс промолчал"
    assert not jargon_in(page_text(app, folded=False))


def test_failed_run_is_explained_plainly(app: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой сборки: на виду — что случилось, причина — в подробностях."""

    def сбой(*args, **kwargs):
        raise RuntimeError("soffice exited with code 1 while rendering deck")

    monkeypatch.setattr("dpd.orchestrator.run_pipeline", сбой)
    upload(app, template_file())
    app.text_area[0].set_value(OUTLINE)
    app.button[0].click().run()

    assert not app.exception, app.exception
    assert app.error, "сбой не показан"
    на_виду = page_text(app, folded=False)
    assert "soffice" not in на_виду, "текст исключения стоит на виду"
    assert not jargon_in(на_виду)
    assert "soffice" in page_text(app, folded=True), "причину сбоя не найти"
