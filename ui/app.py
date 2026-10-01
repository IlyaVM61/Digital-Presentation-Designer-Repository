"""Интерфейс сервиса: загрузка шаблона, диагностика, план, прогон (T-38).

Streamlit выбран решением D6 (ADR-0005): сценарий многошаговый и с
состоянием — загрузка, диагностика, план, прогресс, варианты, находки,
экспорт, — а это сильная сторона Streamlit. Отдельного фронтенда нет и не
предполагается.

**Страница ничего не считает сама.** Она вызывает оркестратор и показывает
его события. Логика пайплайна внутри интерфейса означала бы вторую
реализацию рядом с первой, и расходиться они начали бы с первой правки.

**Диагностика показывается до сборки.** Шаблон может оказаться непригодным:
один макет, размеченный одним заголовком, или тема, врущая про гарнитуру.
Узнать это до того, как ждать полминуты, честнее, чем после.

**Текст слайдов пишет модель по контент-пакету** (T-58): бриф и материалы
загружаются файлами, `draft` оркестратора пишет колоду и проверяет её текст
до вёрстки — тем же путём, что батч сдачи. План, написанный руками, остался
вторым путём: он собирается без модели и без сети (NFR-7).

**Модель смотрит на слайды выбранного варианта по кнопке** (T-52, ADR-0002,
п. 4): это полминуты и запрос на слайд, а варианты переключают, чтобы
сравнить, — смотреть каждый при переключении значило бы втрое больше
ожидания и запросов.

**Варианты выбираются после того, как их увидели** (T-39). Три варианта
стоят рядом, строка на слайд, а файлы всех трёх готовы с прогона: выбор
переключает кнопки скачивания и ничего не пересобирает.

**Страница говорит словами пользователя** (T-55). Пользователь — маркетолог,
а не типограф: «слот», «кегль», «ось варианта» ему мешают. На виду —
человеческая формулировка, подробности для дизайнера свёрнуты, но не
удалены: на них держится правило «назвать свой выбор» (ADR-0006). Ошибка
тоже объясняется фразой, а её техническая причина лежит в подробностях.
Словарь запрещённых на виду слов — в `tests/test_ui_app.py`.

**Что исправить, решает пользователь** (T-40, решение D5). Механические
находки система исправила до показа; на виду остаются те, где способ меняет
слайд по-своему. У каждой — только применимые действия с последствием, по
умолчанию «оставить как есть». После исправления проверка выполняется заново,
и файлы к скачиванию уже содержат правку.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from collections import Counter
from pathlib import Path

import streamlit as st

from dpd.audit.remedies import plain_title
from dpd.generation import ContentPack, structure_from_outline
from dpd.llm import build_client, load_prompts, load_settings
from dpd.models import AuditReport, Finding
from dpd.models.audit import SEVERITY_ORDER
from dpd.orchestrator import (
    DRAFT_TITLES,
    STAGE_TITLES,
    Revision,
    RunResult,
    draft,
    look,
    revise,
    run_pipeline,
)
from dpd.render import soffice_path

FORMATS = [("pptx", "PowerPoint (.pptx)"), ("pdf", "PDF (.pdf)"), ("html", "HTML (.html)")]
MIME = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
    "html": "text/html",
}

FROM_PACK = "Написать по брифу и материалам"
FROM_PLAN = "Взять мой план"

# Проверки, для которых модель смотрит на изображения слайдов (T-52). Пока
# они названы в отчёте пропущенными, на этот вариант модель не смотрела.
VISUAL_CHECKS = ("content.irrelevant_imagery", "content.visual_readability")

PLACEHOLDER_OUTLINE = """Итоги пилота
Что изменилось за квартал
- Срок сборки колоды сократился с девяти дней до трёх
- Правки текста переживают сохранение файла
"""


def output_dir() -> Path:
    """Каталог результатов. На системном диске мало места, поэтому он внешний."""
    base = Path(os.environ.get("DPD_OUTPUT_DIR", "D:/dpd-out")) / "ui"
    base.mkdir(parents=True, exist_ok=True)
    return base


@st.cache_data(show_spinner=False)
def diagnose(template_bytes: bytes, name: str) -> dict[str, object]:
    """Разобрать шаблон и вернуть то, что стоит показать до сборки.

    Кеш по содержимому файла: разбор занимает десятые доли секунды, но
    Streamlit перезапускает скрипт на каждое действие, и повторять его на
    каждый щелчок незачем.
    """
    from dpd.parsing import parse_template

    # Имя файла сохраняется как есть: от него зависят имена готовых файлов,
    # и пользователь должен узнать в них свой шаблон, а не служебный номер.
    folder = Path(tempfile.gettempdir()) / f"dpd-{abs(hash(template_bytes))}"
    folder.mkdir(exist_ok=True)
    path = folder / Path(name).name
    path.write_bytes(template_bytes)

    schema = parse_template(path)
    tokens = schema.design_tokens
    leading = tokens.fonts[0] if tokens and tokens.fonts else None
    return {
        "path": path,
        "layouts": len(schema.layouts),
        "slots": sum(len(layout.slots) for layout in schema.layouts),
        "font": leading.family if leading else "не определён",
        "font_confidence": leading.confidence if leading else 0.0,
        # Тема файла расходится с оформлением во всех четырёх проверенных
        # шаблонах. Расхождение показывается: по нему видно, на чём основано
        # решение системы поверить разметке, а не объявлению темы.
        "theme_fonts": sorted(set(leading.conflicts_with.values())) if leading else [],
        "colors": [token.value for token in tokens.colors[:6]] if tokens else [],
        "scale": tokens.type_scale.values[:8] if tokens else [],
        "strategy": schema.markup_quality.strategy,
        "quality": schema.markup_quality.score,
    }


def fixed(report: AuditReport) -> int:
    """Сколько находок система исправила сама."""
    return len([item for item in report.findings if item.status == "autofixed"])


HEX = re.compile(r"#?[0-9A-Fa-f]{6}")

# Признаки различия вариантов словами пользователя. Ключи — имена из
# `evidence` проверки `variants.low_distinction`; незнакомый признак
# называется общим словом, а не своим внутренним именем.
#
# Комментарием, а не строкой после присваивания: отдельно стоящую строку
# Streamlit выводит на страницу («магия»), и она стояла над заголовком.
AXIS_WORDS = {
    "макетов": "расположением блоков",
    "цветовых схем": "расцветкой",
    "кеглей": "размером текста",
}


def swatches(colors: list[str]) -> str:
    """Цвета шаблона образцами: фирменный цвет узнают глазом, а не по коду.

    В разметку попадает только то, что похоже на цвет: значение пришло из
    чужого файла, а страница вставляет его как HTML.
    """
    return " ".join(
        f'<span title="{value}" style="display:inline-block;width:1.4em;height:1.4em;'
        f'vertical-align:middle;border-radius:4px;border:1px solid #8886;'
        f'background:#{value.lstrip("#")}"></span>'
        for value in colors
        if HEX.fullmatch(value)
    )


def enumerate_words(words: list[str]) -> str:
    """«А», «А и Б», «А, Б и В»."""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " и " + words[-1]


def similarity_note(result: RunResult) -> str:
    """Предупреждение о похожих вариантах — чем не различаются, по-человечески.

    Формулировка проверки («работает осей: 2 из 3») остаётся в подробностях:
    она точна, но написана для того, кто знает, что такое ось регистра.
    """

    def words(key: str) -> list[str]:
        названия = [
            AXIS_WORDS.get(name, "оформлением")
            for finding in result.distinction
            for name in finding.evidence.get(key, [])
        ]
        return list(dict.fromkeys(названия))

    нет, есть = words("missing"), words("working")
    текст = "Варианты получились похожими"
    if нет:
        текст += f": они не различаются {enumerate_words(нет)}"
    текст += ". Дело в шаблоне — другого оформления в нём нет, и взять его неоткуда. "
    if есть:
        return текст + f"Различаются они {enumerate_words(есть)} — посмотрите, хватит ли этого."
    return текст + "Фактически это один и тот же вариант."


def show_details(title: str, error: BaseException) -> None:
    """Причина сбоя — в подробностях: пригодится разработчику, а не пользователю."""
    with st.expander(title):
        st.code(f"{type(error).__name__}: {error}", language=None)


# Критичность словами пользователя: критичное нельзя не заметить, а
# рекомендацию не стоит выдавать за ошибку.
SEVERITY_WORDS = {"critical": "Важно", "warning": "Стоит поправить", "advice": "Совет"}

# Что сказать о замечании, для которого готового способа нет.
NO_REMEDY = {
    "lossy": "Готового способа нет — поправьте в PowerPoint или оставьте как есть.",
    "semantic": "Здесь нужен другой текст — поправьте материалы или план и соберите заново.",
    "none": "Решение за вами: система только сообщает.",
    "mechanical": "Исправить автоматически не получилось — поправьте в PowerPoint.",
}

KEEP = "Оставить как есть"


def where(finding: Finding) -> str:
    return f"Слайд {finding.slide_number}" if finding.slide_number else "Вся презентация"


def show_revision(revision: Revision) -> None:
    """Итог исправления: что сделано и что показала повторная проверка."""
    def counted(counts: dict[str, int]) -> str:
        return ", ".join(f"«{SEVERITY_WORDS[key]}»: {value}" for key, value in counts.items()) or "замечаний нет"

    if revision.applied:
        # Счёт по критичности, а не общий: предупреждение, сменившееся
        # советом, — улучшение, а «было 2, стало 2» читалось бы как ничего.
        только_советы = set(revision.after) == {"advice"}
        st.success(
            f"Исправлено: {len(revision.applied)}. Проверили заново. Было — "
            f"{counted(revision.before)}. Стало — {counted(revision.after)}."
            + (" Остались только советы — их можно не трогать." if только_советы else "")
            + " Файлы к скачиванию обновлены."
        )
    if revision.rejected:
        st.warning(
            "Не стали применять — слайд стал бы хуже, чем был: "
            + "; ".join(
                f"{where(choice.finding).lower()}, «{choice.remedy.title.lower()}»"
                for choice in revision.rejected
            )
        )


def choose_fixes(result: RunResult, variant: str) -> None:
    """Замечания выбранного варианта и выбор, что с ними сделать.

    Номер замечания в отчёте — ключ выбора: по нему оркестратор находит и
    замечание, и подобранные для него способы. Ключ виджета несёт счётчик
    исправлений, чтобы после исправления выбор начинался заново.
    """
    report = result.reports[variant]
    offered = result.remedies.get(variant, {})
    shown = sorted(
        (
            (index, finding)
            for index, finding in enumerate(report.findings)
            if finding.status != "autofixed"
        ),
        key=lambda item: (SEVERITY_ORDER.index(item[1].severity), item[1].slide_number or 0),
    )

    st.subheader("Что стоит поправить")
    if result.revision is not None and result.revision.variant == variant:
        show_revision(result.revision)
    if not shown:
        st.markdown("Замечаний к этому варианту нет.")
        return

    chosen: dict[int, str] = {}
    for index, finding in shown:
        title = plain_title(finding)
        # Место — в конце: часть названий сама начинается со «Слайд», и
        # «Слайд 2: слайд заполнен…» читалось бы запинкой.
        label = f"{SEVERITY_WORDS[finding.severity]}: {title[:1].lower()}{title[1:]} — {where(finding).lower()}"
        remedies = offered.get(index, [])
        if not remedies:
            st.markdown(f"- {label}. {NO_REMEDY.get(finding.fixability, NO_REMEDY['none'])}")
            continue
        answer = st.radio(
            label,
            [KEEP, *(item.title for item in remedies)],
            captions=["Ничего не меняется", *(item.consequence for item in remedies)],
            key=f"fix-{result.run_id}-{result.revisions}-{variant}-{index}",
        )
        if answer != KEEP:
            chosen[index] = next(item.id for item in remedies if item.title == answer)

    with st.expander("Подробности для дизайнера"):
        for finding in report.findings:
            mark = "исправлено автоматически: " if finding.status == "autofixed" else ""
            st.markdown(f"- `{finding.check_id}` — {mark}{finding.message}")

    if not offered:
        return
    if st.button("Исправить выбранное", key="apply-fixes"):
        if not chosen:
            st.info("Ничего не выбрано — всё осталось как есть.")
            return
        with st.spinner("Исправляем и проверяем заново"):
            try:
                revised = revise(result, variant, chosen)
            except Exception as error:  # noqa: BLE001 — пользователю нужна причина, а не трассировка
                st.error("Исправить не получилось. Файлы остались прежними.")
                show_details("Подробности сбоя", error)
                return
        st.session_state["result"] = revised
        st.rerun()


def offer_look(result: RunResult, variant: str) -> None:
    """Модель смотрит на слайды выбранного варианта (T-52) — по кнопке.

    Её замечания дописываются в список «Что стоит поправить». После
    исправления слайды другие, и кнопка появляется снова: прежний взгляд
    модели к ним уже не относится.
    """
    report = result.reports[variant]
    if not set(VISUAL_CHECKS) & set(report.checks_skipped):
        секунд = report.timings.get("visual")
        st.caption(
            "Модель посмотрела на слайды этого варианта"
            + (f" за {секунд:.0f} с" if секунд else "")
            + ": что она заметила, — в списке ниже."
        )
        return
    if not st.button(
        "Проверить, как выглядят слайды",
        key="look",
        help="Модель смотрит на каждый слайд выбранного варианта: виден ли текст и к месту ли картинки. "
        "Около полуминуты, нужен интернет.",
    ):
        return
    with st.spinner("Модель смотрит на слайды"):
        try:
            prompts = load_prompts()
            looked = look(result, variant, build_client(load_settings("vlm"), prompts), prompts)
        except Exception as error:  # noqa: BLE001 — пользователю нужна причина, а не трассировка
            st.error("Посмотреть на слайды не получилось. Остальные проверки выполнены, файлы готовы к скачиванию.")
            show_details("Подробности сбоя", error)
            return
    st.session_state["result"] = looked
    st.rerun()


def show_side_by_side(result: RunResult) -> None:
    """Показать варианты рядом: строка на слайд, колонка на вариант (T-39).

    Критерий приёмки — различие видно без пояснений. Поэтому один и тот же
    слайд трёх вариантов стоит в одной строке, а не за переключателем:
    сравнение по памяти различия не показывает, а только утверждает.
    """
    # Изображения бывают не у всех: модель, посмотрев на выбранный вариант,
    # рисует только его, — а одна колонка из трёх сравнения не даёт.
    if len(result.variant_previews) < len(result.variants):
        st.info(
            "Превью выключено: сравнить варианты можно по скачанным файлам. "
            "Включите «Превью слайдов», чтобы увидеть их рядом."
        )
        return

    шапка = st.columns(len(result.variants))
    for column, deck in zip(шапка, result.variants, strict=True):
        column.markdown(f"**{result.variant_names.get(deck.variant, deck.variant)}**")

    слайдов = max(len(images) for images in result.variant_previews.values())
    for index in range(слайдов):
        ряд = st.columns(len(result.variants))
        for column, deck in zip(ряд, result.variants, strict=True):
            images = result.variant_previews.get(deck.variant, [])
            if index < len(images):
                column.image(str(images[index]), width="stretch")


st.set_page_config(page_title="Цифровой дизайнер презентаций", layout="wide")
st.title("Цифровой дизайнер презентаций")
st.caption(
    "Собирает новые слайды в оформлении вашего шаблона: шрифты, цвета и макеты "
    "берутся из него. Файл не покидает этот компьютер."
)

uploaded = st.file_uploader("Шаблон презентации", type=["pptx"])

report = None
if uploaded is not None:
    try:
        report = diagnose(uploaded.getvalue(), uploaded.name)
    except Exception as error:  # noqa: BLE001 — пользователю нужна фраза, а не трассировка
        st.error(
            "Этот файл не получилось открыть как презентацию. Проверьте, что это `.pptx` "
            "и что он открывается в PowerPoint, — или загрузите другой шаблон."
        )
        show_details("Подробности ошибки", error)

if report is not None:
    st.subheader("Что удалось прочитать в шаблоне")
    шрифт = f"**Шрифт:** {report['font']}"
    if report["theme_fonts"]:
        # Тема файла расходится с оформлением во всех проверенных шаблонах.
        # Выбор системы называется на виду: это правило владельца (ADR-0006).
        шрифт += (
            f" — так на слайдах шаблона. В теме оформления файла указан "
            f"{', '.join(report['theme_fonts'])}, но система берёт то, что на слайдах."
        )
    цвета = swatches(report["colors"]) or "не определены"
    st.markdown(
        f"**Макетов слайдов:** {report['layouts']}  \n{шрифт}  \n**Фирменные цвета:** {цвета}",
        unsafe_allow_html=True,
    )

    if report["font_confidence"] < 0.4:
        st.warning(
            "Шрифты в шаблоне смешаны, и ни один не преобладает. Система взяла самый "
            "частый, но это предположение, поэтому замечания проверки будут советами, "
            "а не требованиями."
        )

    with st.expander("Подробности для дизайнера"):
        тема = ", ".join(report["theme_fonts"]) or "совпадает с разметкой"
        палитра = " ".join(f"`{value}`" for value in report["colors"]) or "не определена"
        шкала = ", ".join(f"{value:g}" for value in report["scale"]) or "не определена"
        st.markdown(
            f"**Макетов:** {report['layouts']}, слотов в них {report['slots']}  \n"
            f"**Ведущая гарнитура:** {report['font']}, уверенность "
            f"{report['font_confidence']:.2f}; тема объявляет: {тема}  \n"
            f"**Стратегия разбора:** {report['strategy']}, качество разметки {report['quality']:.2f}  \n"
            f"**Палитра:** {палитра}  \n**Шкала кеглей:** {шкала} pt"
        )

st.subheader("Содержание")
источник = st.radio(
    "Откуда взять текст слайдов",
    [FROM_PACK, FROM_PLAN],
    captions=[
        "Модель напишет текст по вашим файлам — это несколько минут",
        "Заголовки и пункты пишете вы, модель не нужна",
    ],
    horizontal=True,
    key="source",
)
по_материалам = источник == FROM_PACK
brief = content = None
outline = ""
if по_материалам:
    st.caption(
        "Бриф — для кого и зачем презентация, сколько в ней слайдов. Материалы — тезисы, "
        "цифры, таблицы: каждое число на слайдах модель берёт из них."
    )
    левая, правая = st.columns(2)
    brief = левая.file_uploader("Бриф", type=["md", "txt"], key="brief")
    content = правая.file_uploader("Материалы", type=["md", "txt"], key="content")
else:
    st.caption("Строка без знака — заголовок слайда, строка со знаком «-» — его пункт.")
    outline = st.text_area("План", value=PLACEHOLDER_OUTLINE, height=200, label_visibility="collapsed")

st.subheader("В каких форматах скачать", help=f"Копии файлов сохраняются в {output_dir()}")
st.caption("Файлы готовятся для всех трёх вариантов сразу — выбрать можно после сборки.")
columns = st.columns(len(FORMATS) + 1)
chosen_formats = [
    key
    for (key, label), column in zip(FORMATS, columns, strict=False)
    if column.checkbox(label, value=key == "pptx")
]
with columns[-1]:
    # Сравнение вариантов глазами — основной путь сценария (PRD, шаг 5), а не
    # опция для знающих. Выключено оно только там, где LibreOffice нет.
    previews = st.checkbox(
        "Превью слайдов",
        value=soffice_path() is not None,
        help="Показывает три варианта рядом, чтобы выбрать глазами. "
        "Нужна программа LibreOffice; добавляет около 20 секунд",
    )

if st.button("Собрать презентацию", type="primary"):
    if uploaded is None or report is None:
        st.warning("Сначала загрузите шаблон `.pptx` — без него собирать не по чему.")
    elif по_материалам and (brief is None or content is None):
        st.warning("Загрузите бриф и материалы — без них модели не из чего писать текст.")
    elif not по_материалам and not outline.strip():
        st.warning("План презентации пуст. Напишите хотя бы один заголовок.")
    elif not chosen_formats:
        st.warning("Выберите хотя бы один формат выгрузки.")
    else:
        bar = st.progress(0.0, text="Начинаем")
        названия = {**DRAFT_TITLES, **STAGE_TITLES} if по_материалам else STAGE_TITLES
        стадии = {name: 0.0 for name in названия}

        def show(stage: str, done: int, total: int) -> None:
            """Прогресс считается по этапам: пользователь ждёт десятки секунд."""
            стадии[stage] = done / total if total else 1.0
            доля = sum(стадии.values()) / len(стадии)
            bar.progress(min(доля, 1.0), text=названия.get(stage, stage))

        started = time.perf_counter()
        пишем = по_материалам
        try:
            if по_материалам:
                pack = ContentPack(
                    brief=brief.getvalue().decode("utf-8-sig").strip(),
                    content=content.getvalue().decode("utf-8-sig").strip(),
                )
                prompts = load_prompts()
                drafted = draft(pack, build_client(load_settings("llm"), prompts), prompts, progress=show)
                structure, text_audit, review_error = drafted.structure, drafted.text_audit, drafted.review_error
            else:
                structure, text_audit, review_error = structure_from_outline(outline), None, None
            пишем = False
            result = run_pipeline(
                report["path"],
                structure,
                output_dir() / time.strftime("%Y%m%d-%H%M%S"),
                formats=tuple(chosen_formats),
                previews=previews,
                progress=show,
                text_audit=text_audit,
            )
        except Exception as error:  # noqa: BLE001 — пользователю нужна причина, а не трассировка
            bar.empty()
            st.session_state.pop("result", None)
            if пишем:
                st.error(
                    "Написать текст презентации не получилось. Проверьте "
                    "интернет и попробуйте ещё раз — или соберите по своему плану. Если сбой "
                    "повторится, передайте разработчикам подробности ниже."
                )
            else:
                st.error(
                    "Собрать презентацию не получилось. Попробуйте ещё раз; если сбой "
                    "повторится, передайте разработчикам подробности ниже."
                )
            show_details("Подробности сбоя", error)
        else:
            bar.progress(1.0, text="Готово")
            # Итог кладётся в состояние страницы, а не показывается здесь же:
            # нажатие «Скачать» — тоже действие, страница перезапускается, и
            # вместе с ней исчезли бы и остальные кнопки, и отчёт аудита.
            st.session_state["result"] = result
            st.session_state["seconds"] = time.perf_counter() - started
            st.session_state["review_error"] = review_error

result = st.session_state.get("result")
if result is not None:
    st.divider()
    st.success(f"Презентация готова за {st.session_state.get('seconds', 0.0):.1f} с")

    with st.expander("Из чего сложилось время"):
        st.markdown(
            ", ".join(
                f"{({**DRAFT_TITLES, **STAGE_TITLES}).get(stage, stage)} — {seconds:.1f} с"
                for stage, seconds in result.timings.items()
            )
        )

    if st.session_state.get("review_error"):
        # Пропуск называется на виду: без него «замечаний к тексту нет»
        # читалось бы как проверенный текст.
        st.warning(
            "Смысл текста проверить не удалось — модель не ответила. Оформление проверено, "
            "файлы готовы; текст стоит перечитать самим."
        )
        with st.expander("Подробности сбоя проверки текста"):
            st.code(st.session_state["review_error"], language=None)

    находки = sum(len(report_.findings) for report_ in result.reports.values())
    исправлено = sum(fixed(report_) for report_ in result.reports.values())
    st.markdown(
        f"**Проверка оформления:** замечаний {находки}, исправлено автоматически "
        f"{исправлено} — по всем трём вариантам."
    )

    if result.distinction:
        # Три одинаковые колонки без объяснения — худшее, что можно показать:
        # пользователь решит, что система сломалась, или не заметит подмены.
        st.warning(similarity_note(result))
        with st.expander("Подробности проверки"):
            for finding in result.distinction:
                st.markdown(f"- {finding.message}")

    st.subheader("Три варианта")
    show_side_by_side(result)

    варианты = [deck.variant for deck in result.variants]
    выбран = st.radio(
        "Какой вариант берёте",
        options=варианты,
        index=варианты.index(result.chosen.variant),
        format_func=lambda key: result.variant_names.get(key, key),
        captions=[
            f"замечаний {len(result.reports[key].findings)}, "
            f"исправлено автоматически {fixed(result.reports[key])}"
            for key in варианты
        ],
        horizontal=True,
        key="variant",
    )
    колода = next(deck for deck in result.variants if deck.variant == выбран)

    if колода.decisions:
        # Система не вправе молча менять оформление: она называет, что
        # выбрала и что объявляет сам шаблон. Подробности свёрнуты — человек
        # пришёл делать презентацию, а не разбираться в наследовании стилей.
        # Одинаковые решения по разным блокам сводятся в одну строку: двадцать
        # повторов «шрифт Play вместо Arial» ничего не сообщают.
        группы = Counter((item.kind, item.chosen, item.declared) for item in колода.decisions)
        with st.expander(f"Где шаблон противоречит себе, система выбрала сама: {len(группы)}"):
            for (kind, chosen, declared), count in группы.items():
                что = "Шрифт" if kind == "font" else "Размер текста"
                мест = f" (мест на слайдах: {count})" if count > 1 else ""
                st.markdown(
                    f"- {что} {chosen} — так на слайдах шаблона; в его настройках "
                    f"указано {declared}{мест}"
                )
            st.caption(
                "Для дизайнера: "
                + "; ".join(sorted({item.reason for item in колода.decisions}))
                + ". Переключить правило можно в configs/layout.yaml: "
                "`conflict_rule: template` заставит следовать объявлению шаблона."
            )

    offer_look(result, выбран)
    choose_fixes(result, выбран)

    # Файлы всех трёх вариантов готовы с прогона: выбор переключает кнопки,
    # а не запускает сборку заново.
    for key, path in result.variant_exports.get(выбран, {}).items():
        st.download_button(
            f"Скачать {key.upper()} — {path.name}",
            data=path.read_bytes(),
            file_name=path.name,
            mime=MIME.get(key, "application/octet-stream"),
        )
