"""Интерфейс Streamlit: T-38 — сценарий, T-39 — три варианта, T-55 — язык, T-40 — находки.

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
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
import streamlit as st
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
def out_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    # Через monkeypatch, а не os.environ: без восстановления каждый тест
    # вкладывал `tests/ui` в каталог предыдущего, и к третьему десятку тестов
    # путь превышал предел длины пути Windows.
    base = Path(os.environ.get("DPD_OUTPUT_DIR", "D:/dpd-out")) / "tests" / "ui"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DPD_OUTPUT_DIR", str(base))
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


def write_plan(app: AppTest, text: str) -> None:
    """Текст слайдов — своим планом, без модели: путь детерминированного контура.

    По умолчанию текст пишет модель (T-58), и поле плана появляется только
    после выбора этого пути.
    """
    source = app.radio(key="source")
    source.set_value(source.options[1]).run()
    app.text_area[0].set_value(text)


# --- Страница открывается --------------------------------------------------


def test_page_offers_upload_and_plan(app: AppTest) -> None:
    """До загрузки шаблона страница объясняет, чего ждёт: шаблон и откуда
    взять текст — материалы для модели или свой план."""
    app.run()

    assert not app.exception
    assert app.title[0].value.startswith("Цифровой дизайнер")
    assert len(app.file_uploader) == 3, "нет полей шаблона, брифа и материалов"
    assert len(app.radio(key="source").options) == 2

    write_plan(app, OUTLINE)

    assert not app.exception
    assert app.text_area, "нет поля плана колоды"


def test_page_says_truly_where_files_go(app: AppTest) -> None:
    """T-74: страница работает и на сервере для экспертов, а там «файл не
    покидает этот компьютер» — неправда: шаблон загружается на сервер. Правда
    в обоих случаях одна — что уходит модели, а что нет."""
    app.run()

    text = " ".join(caption.value for caption in app.caption)
    assert "не покидает этот компьютер" not in text
    assert "модел" in text, "страница не говорит, что уходит модели"


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


def test_uploaded_template_is_kept_once_beside_the_results(
    out_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Копия шаблона лежит в каталоге результатов, одна на шаблон (T-63).

    Прежде она ложилась во временный каталог системы, на диск C, под ключом,
    который менялся с каждым процессом: 99 копий за два дня. Кеш разбора
    сбрасывается, чтобы страница действительно прошла путь загрузки, а не
    взяла итог прошлого теста.
    """
    system = tmp_path / "system-temp"
    system.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system))
    st.cache_data.clear()

    first = upload(AppTest.from_file(str(APP), default_timeout=180), template_file())
    assert not first.exception, first.exception
    copies = sorted(out_dir.rglob(TEMPLATE))

    st.cache_data.clear()
    again = upload(AppTest.from_file(str(APP), default_timeout=180), template_file())
    assert not again.exception, again.exception

    assert not any(system.iterdir()), "копия шаблона легла во временный каталог системы"
    assert len(copies) == 1, f"копий шаблона в каталоге результатов: {len(copies)}"
    assert sorted(out_dir.rglob(TEMPLATE)) == copies, "повторная загрузка сделала новую копию"


# --- Сценарий от файла до экспорта -----------------------------------------


def test_scenario_runs_from_file_to_exported_files(app: AppTest) -> None:
    """Критерий приёмки задачи целиком.

    Берутся `.pptx` и `.html`: оба быстрые. PDF требует LibreOffice и
    десятков секунд — он проверен в тестах оркестратора и экспорта.
    """
    upload(app, template_file())
    write_plan(app, OUTLINE)
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
    write_plan(app, OUTLINE)
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
    """Критерий приёмки T-41: общее время видно пользователю.

    Итог — сразу, разбивка по этапам — в свёрнутом блоке и в порядке хода
    прогона: она нужна, чтобы понять, что не уложилось в пять минут, а
    маркетологу на виду достаточно итога.
    """
    import re

    from dpd.orchestrator import STAGE_TITLES, STAGES

    upload(app, template_file())
    write_plan(app, OUTLINE)
    app.button[0].click().run()

    assert re.search(r"готова за \d+[.,]\d с", page_text(app, folded=False)), "общего времени не видно"
    блок = next(item for item in app.expander if item.label == "Из чего сложилось время")
    разбивка = " ".join(item.value for item in блок.markdown)
    этапы = [stage for stage in STAGES if stage in app.session_state["result"].timings]
    позиции = [разбивка.find(STAGE_TITLES[stage]) for stage in этапы]
    assert -1 not in позиции, f"в разбивке не все этапы: {разбивка}"
    assert позиции == sorted(позиции), f"этапы идут не в порядке прогона: {разбивка}"


def test_empty_plan_is_refused_with_an_explanation(app: AppTest) -> None:
    """Пустой план — не колода из нуля слайдов, а несделанная работа."""
    upload(app, template_file())
    write_plan(app, "   ")
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
    write_plan(app, OUTLINE)
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
    выбор = app.radio(key="variant")
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
    "путь к конфигу": r"configs[/\\]|\.yaml\b",
    "внутренний идентификатор": r"\b(?:placeholder|geometry|examples)-first\b|\bconfidence\b|\bemu\b",
    # Так на страницу однажды попал комментарий из кода: отдельно стоящую
    # строку Streamlit выводит сам («магия»), и тесты на слова её не видели.
    "имя проверки или поля": r"\b[a-z]+\.[a-z]+_[a-z_]+\b|\bevidence\b",
}
"""Слова, которые маркетолог встречать не должен. «Макет» сюда не входит:
так называет макеты слайдов сам PowerPoint."""

TEXT_ELEMENTS = {"markdown", "caption", "code", "title", "header", "subheader", "warning", "info", "success", "error"}


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
    app.radio(key="variant").set_value("C").run()

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
    write_plan(app, OUTLINE)
    app.button[0].click().run()

    assert not app.exception, app.exception
    assert app.error, "сбой не показан"
    на_виду = page_text(app, folded=False)
    assert "soffice" not in на_виду, "текст исключения стоит на виду"
    assert not jargon_in(на_виду)
    assert "soffice" in page_text(app, folded=True), "причину сбоя не найти"


# --- T-40: работа с находками ------------------------------------------------
#
# Находки аудита — самая текстовая часть страницы, и словарь T-55 держит их
# так же, как всё остальное. Механические исправлены до показа; на виду
# остаются те, где решать пользователю, и у каждой — только применимые
# действия.

CROWDED = OUTLINE + """Что умеет система
- Разбор шаблона занимает меньше секунды
- Три варианта собираются одним нажатием
- Таблицы остаются таблицами
- Диаграммы остаются диаграммами
- Проверка оформления идёт до выгрузки
- Механические дефекты исправляются сами
- Файлы готовы в трёх форматах
"""


def crowded(app: AppTest) -> AppTest:
    upload(app, template_file())
    write_plan(app, CROWDED)
    for checkbox in app.checkbox:
        checkbox.set_value("pptx" in checkbox.label.lower())
    return app.button[0].click().run()


def fix_choice(app: AppTest):
    """Выбор для переполненного слайда — ищется по словам, которые видит человек."""
    return next((radio for radio in app.radio if "пункт" in radio.label.lower()), None)


def test_finding_can_be_fixed_from_the_page(app: AppTest) -> None:
    """Критерий приёмки T-40 глазами пользователя: выбрал, нажал, проверено заново."""
    crowded(app)
    assert not app.exception, app.exception
    choice = fix_choice(app)
    assert choice is not None, "переполненный слайд не предложен к исправлению"
    assert len(choice.options) == 2, "кроме «оставить как есть» применим один способ"
    перенос = next(option for option in choice.options if option.startswith("Перенести"))

    choice.set_value(перенос)
    app.button(key="apply-fixes").click().run()

    assert not app.exception, app.exception
    assert fix_choice(app) is None, "находка осталась после исправления"
    итог = " ".join(item.value for item in app.success)
    assert "проверили заново" in итог.lower(), итог


def test_findings_speak_plain_language(app: AppTest) -> None:
    """Находки и действия на виду проходят словарь — до исправления и после."""
    crowded(app)
    assert not app.exception, app.exception
    assert not jargon_in(page_text(app, folded=False))

    choice = fix_choice(app)
    choice.set_value(next(option for option in choice.options if option.startswith("Перенести")))
    app.button(key="apply-fixes").click().run()

    assert not app.exception, app.exception
    assert not jargon_in(page_text(app, folded=False))


def test_finding_details_are_folded_not_lost(app: AppTest) -> None:
    """Исходная формулировка проверки — для дизайнера, в свёрнутом блоке."""
    crowded(app)

    свёрнуто = page_text(app, folded=True)
    assert "density.too_many_bullets" in свёрнуто
    assert "список перестаёт читаться" in свёрнуто


def test_leaving_everything_as_is_is_a_valid_choice(app: AppTest) -> None:
    """«Оставить как есть» — выбор по умолчанию, и он ничего не ломает."""
    crowded(app)
    app.button(key="apply-fixes").click().run()

    assert not app.exception, app.exception
    assert fix_choice(app) is not None, "находка пропала, хотя ничего не выбрано"


# --- T-58: колода по контент-пакету из интерфейса ----------------------------
#
# Критерий приёмки: со страницы загружаются шаблон и контент-пакет, колоду
# пишет модель, аудит текста и аудит визуала выбранного варианта
# выполняются, файлы скачиваются. Модели подменены подделкой батча: тест
# проверяет страницу, а не модель, — модель проверяется живым прогоном.

PACK = Path(__file__).resolve().parents[1] / "assets" / "content-pack"
VISUAL = ("content.irrelevant_imagery", "content.visual_readability")


def offline_models(monkeypatch: pytest.MonkeyPatch, *, refuse: str | None = None) -> list[str]:
    """Обе роли отвечают подделкой; `refuse` — схема ответа, на которой провайдер
    отказывает (`"*"` — на любой). Возвращает схемы запрошенных ответов."""
    import json

    import httpx
    from test_batch import SETTINGS, fake_model

    from dpd import llm

    real = llm.build_client
    asked: list[str] = []

    def reply(request: httpx.Request) -> httpx.Response:
        name = json.loads(request.content)["response_format"]["json_schema"]["name"]
        asked.append(name)
        if refuse in (name, "*"):
            return httpx.Response(401, json={"error": {"message": "No auth credentials found"}})
        return fake_model(request)

    monkeypatch.setattr(llm, "build_client", lambda settings, prompts, **_: real(SETTINGS, prompts, transport=httpx.MockTransport(reply)))
    return asked


def from_pack(app: AppTest, *, pack: bool = True) -> AppTest:
    """Шаблон и контент-пакет — так же, как их выберет пользователь, — и сборка."""
    upload(app, template_file())
    if pack:
        for key in ("brief", "content"):
            app.file_uploader(key=key).upload(f"{key}.md", (PACK / f"{key}.md").read_bytes(), "text/markdown")
        app.run()
    for checkbox in app.checkbox:
        if checkbox.label.startswith("Превью"):
            checkbox.set_value(False)
        else:
            checkbox.set_value(any(fmt in checkbox.label.lower() for fmt in ("pptx", "html")))
    return app.button[0].click().run()


def test_deck_is_written_from_the_content_pack_and_downloaded(app: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Критерий T-58 целиком, глазами пользователя."""
    from test_batch import GENERATED_BODY, fake_render

    asked = offline_models(monkeypatch)
    fake_render(monkeypatch)
    from_pack(app)

    assert not app.exception, app.exception
    result = app.session_state["result"]
    assert any(slide.body and GENERATED_BODY in slide.body.items for slide in result.structure.slides)
    # Аудит текста — до вёрстки, в отчёте каждого варианта; модель смотрит
    # на слайды только после выбора (ADR-0002, п. 4).
    assert {"DeckOutline", "TextReview"} <= set(asked)
    assert "SlideLook" not in asked
    for report in result.reports.values():
        assert "content.headline_no_conclusion" in report.checks_run
        assert set(VISUAL) <= set(report.checks_skipped)

    app.radio(key="variant").set_value("B").run()
    app.button(key="look").click().run()

    assert not app.exception, app.exception
    assert "SlideLook" in asked
    looked = app.session_state["result"].reports
    assert set(VISUAL) <= set(looked["B"].checks_run)
    assert set(VISUAL) <= set(looked["A"].checks_skipped), "смотрели не только выбранный вариант"
    assert "look" not in [button.key for button in app.button], "просмотренный вариант предлагают смотреть снова"
    имена = [button.label for button in app.download_button]
    assert len(имена) == 2 and all("-B." in name for name in имена), имена
    assert not jargon_in(page_text(app, folded=False))


def test_writing_time_is_in_the_breakdown(app: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Генерация — самая долгая часть цикла (536 с в T-43, из них 260 —
    текст), и в разбивке времени она стоит первой, как шла."""
    from dpd.orchestrator import DRAFT_TITLES, STAGE_TITLES

    offline_models(monkeypatch)
    from_pack(app)

    assert not app.exception, app.exception
    блок = next(item for item in app.expander if item.label == "Из чего сложилось время")
    разбивка = " ".join(item.value for item in блок.markdown)
    позиции = [разбивка.find(title) for title in (*DRAFT_TITLES.values(), STAGE_TITLES["parse"])]
    assert -1 not in позиции and позиции == sorted(позиции), разбивка


def test_content_pack_is_required_before_any_request(app: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Без брифа и материалов модели не из чего писать — запрос не уходит."""
    asked = offline_models(monkeypatch)
    from_pack(app, pack=False)

    assert not app.exception, app.exception
    assert app.warning, "интерфейс не объяснил, чего не хватает"
    assert asked == []
    assert "result" not in app.session_state


def test_failed_writing_is_explained_plainly(app: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Модель недоступна: на виду — что делать, причина — в подробностях."""
    offline_models(monkeypatch, refuse="*")
    from_pack(app)

    assert not app.exception, app.exception
    assert app.error, "сбой не показан"
    assert "result" not in app.session_state
    на_виду = page_text(app, folded=False)
    assert "HTTP 401" not in на_виду
    assert not jargon_in(на_виду)
    assert "HTTP 401" in page_text(app, folded=True), "причину сбоя не найти"


def test_failed_text_check_is_reported_not_hidden(app: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка текста не удалась — колода собирается, но пользователь
    узнаёт, что смысл текста не проверен, а не думает, что замечаний нет."""
    offline_models(monkeypatch, refuse="TextReview")
    from_pack(app)

    assert not app.exception, app.exception
    assert app.download_button, "колода не собрана"
    assert app.warning, "пропуск проверки текста не показан"
    assert not jargon_in(page_text(app, folded=False))
    assert "HTTP 401" in page_text(app, folded=True)


def test_failed_look_keeps_the_files(app: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Модель не посмотрела на слайды — файлы остаются, сбой объяснён."""
    from test_batch import fake_render

    offline_models(monkeypatch, refuse="SlideLook")
    fake_render(monkeypatch)
    from_pack(app)
    app.button(key="look").click().run()

    assert not app.exception, app.exception
    assert app.error, "сбой не показан"
    assert app.download_button, "файлы пропали"
    assert set(VISUAL) <= set(app.session_state["result"].reports["A"].checks_skipped)
    assert not jargon_in(page_text(app, folded=False))
