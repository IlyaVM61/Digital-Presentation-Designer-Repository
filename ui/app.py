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

**План колоды пока вводится руками.** Слой генерации по брифу — задача T-49;
до неё структуру приносит пользователь, и подменять это правдоподобной
заглушкой нельзя: пользователь решил бы, что модель уже работает.

**Варианты выбираются после того, как их увидели** (T-39). Три варианта
стоят рядом, строка на слайд, а файлы всех трёх готовы с прогона: выбор
переключает кнопки скачивания и ничего не пересобирает.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import streamlit as st

from dpd.generation import structure_from_outline
from dpd.models import AuditReport
from dpd.orchestrator import STAGE_TITLES, RunResult, run_pipeline
from dpd.render import soffice_path

FORMATS = [("pptx", "PowerPoint (.pptx)"), ("pdf", "PDF (.pdf)"), ("html", "HTML (.html)")]
MIME = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
    "html": "text/html",
}

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

    path = Path(tempfile.gettempdir()) / f"dpd-{abs(hash(template_bytes))}-{name}"
    path.write_bytes(template_bytes)

    schema = parse_template(path)
    tokens = schema.design_tokens
    leading = tokens.fonts[0] if tokens and tokens.fonts else None
    return {
        "path": path,
        "layouts": len(schema.layouts),
        "slots": sum(len(layout.slots) for layout in schema.layouts),
        "font": leading.family if leading else "не определена",
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


def show_side_by_side(result: RunResult) -> None:
    """Показать варианты рядом: строка на слайд, колонка на вариант (T-39).

    Критерий приёмки — различие видно без пояснений. Поэтому один и тот же
    слайд трёх вариантов стоит в одной строке, а не за переключателем:
    сравнение по памяти различия не показывает, а только утверждает.
    """
    if not result.variant_previews:
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
    "Читает чужой шаблон как набор правил и собирает по нему новые слайды. "
    "Шаблон остаётся у вас: файл не покидает эту машину."
)

uploaded = st.file_uploader("Шаблон презентации", type=["pptx"])

report = None
if uploaded is not None:
    report = diagnose(uploaded.getvalue(), uploaded.name)

    st.subheader("Что удалось прочитать в шаблоне")
    left, right = st.columns(2)
    with left:
        тема = (
            f"тема файла объявляет {', '.join(report['theme_fonts'])} — "
            "система поверила разметке"
            if report["theme_fonts"]
            else "тема файла с разметкой согласна"
        )
        st.markdown(
            f"**Макетов:** {report['layouts']}, слотов в них {report['slots']}  \n"
            f"**Ведущая гарнитура:** {report['font']} "
            f"(уверенность {report['font_confidence']:.2f}); {тема}  \n"
            f"**Стратегия разбора:** {report['strategy']}, качество разметки {report['quality']:.2f}"
        )
    with right:
        палитра = " ".join(f"`{value}`" for value in report["colors"]) or "не определена"
        шкала = ", ".join(f"{value:g}" for value in report["scale"]) or "не определена"
        st.markdown(f"**Палитра:** {палитра}  \n**Шкала кеглей:** {шкала} pt")

    if report["font_confidence"] < 0.4:
        st.warning(
            "Шаблон размечен вразнобой: ведущая гарнитура набирает меньше двух пятых разметки. "
            "Правила выведены предположительно, и аудит понизит находки до рекомендаций."
        )

st.subheader("План колоды")
st.caption(
    "Строка без знака — заголовок слайда, строка со знаком «-» — его пункт. "
    "Пока содержание пишете вы: генерация плана по свободному брифу появится позже."
)
outline = st.text_area("План", value=PLACEHOLDER_OUTLINE, height=200, label_visibility="collapsed")

st.subheader("Форматы выгрузки")
st.caption(f"Готовые файлы сохраняются в {output_dir()} и предлагаются к скачиванию.")
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
        help="Показывает три варианта рядом. Требует LibreOffice, добавляет около 20 с",
    )

if st.button("Собрать колоду", type="primary"):
    if uploaded is None or report is None:
        st.warning("Сначала загрузите шаблон `.pptx` — без него собирать не по чему.")
    elif not outline.strip():
        st.warning("План колоды пуст. Напишите хотя бы один заголовок.")
    elif not chosen_formats:
        st.warning("Выберите хотя бы один формат выгрузки.")
    else:
        bar = st.progress(0.0, text="Начинаем")
        стадии = {name: 0.0 for name in STAGE_TITLES}

        def show(stage: str, done: int, total: int) -> None:
            """Прогресс считается по этапам: пользователь ждёт десятки секунд."""
            стадии[stage] = done / total if total else 1.0
            доля = sum(стадии.values()) / len(стадии)
            bar.progress(min(доля, 1.0), text=STAGE_TITLES.get(stage, stage))

        started = time.perf_counter()
        try:
            result = run_pipeline(
                report["path"],
                structure_from_outline(outline),
                output_dir() / time.strftime("%Y%m%d-%H%M%S"),
                formats=tuple(chosen_formats),
                previews=previews,
                progress=show,
            )
        except Exception as error:  # noqa: BLE001 — пользователю нужна причина, а не трассировка
            bar.empty()
            st.session_state.pop("result", None)
            st.error(f"Прогон не удался: {error}")
        else:
            bar.progress(1.0, text="Готово")
            # Итог кладётся в состояние страницы, а не показывается здесь же:
            # нажатие «Скачать» — тоже действие, страница перезапускается, и
            # вместе с ней исчезли бы и остальные кнопки, и отчёт аудита.
            st.session_state["result"] = result
            st.session_state["seconds"] = time.perf_counter() - started

result = st.session_state.get("result")
if result is not None:
    st.divider()
    st.success(f"Колода готова за {st.session_state.get('seconds', 0.0):.1f} с")

    st.markdown(
        "**Время по этапам:** "
        + ", ".join(
            f"{STAGE_TITLES.get(stage, stage)} — {seconds:.1f} с"
            for stage, seconds in result.timings.items()
        )
    )

    находки = sum(len(report_.findings) for report_ in result.reports.values())
    исправлено = sum(fixed(report_) for report_ in result.reports.values())
    st.markdown(
        f"**Аудит:** собрано вариантов {len(result.variants)}, "
        f"находок {находки}, из них исправлено автоматически {исправлено}."
    )

    if result.distinction:
        # Три одинаковые колонки без объяснения — худшее, что можно показать:
        # пользователь решит, что система сломалась, или не заметит подмены.
        st.warning(
            "Варианты получились похожими: в шаблоне не нашлось, чем их различить. "
            + " ".join(finding.message for finding in result.distinction)
        )

    st.subheader("Три варианта")
    show_side_by_side(result)

    варианты = [deck.variant for deck in result.variants]
    выбран = st.radio(
        "Какой вариант берёте",
        options=варианты,
        index=варианты.index(result.chosen.variant),
        format_func=lambda key: result.variant_names.get(key, key),
        captions=[
            f"находок {len(result.reports[key].findings)}, "
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
        with st.expander(f"Система приняла решений за вас: {len(колода.decisions)}"):
            for decision in колода.decisions:
                st.markdown(f"- {decision.reason} (в шаблоне объявлено: {decision.declared})")
            st.caption(
                "Переключить правило можно в configs/layout.yaml: "
                "`conflict_rule: template` заставит следовать объявлению шаблона."
            )

    # Файлы всех трёх вариантов готовы с прогона: выбор переключает кнопки,
    # а не запускает сборку заново.
    for key, path in result.variant_exports.get(выбран, {}).items():
        st.download_button(
            f"Скачать {key.upper()} — {path.name}",
            data=path.read_bytes(),
            file_name=path.name,
            mime=MIME.get(key, "application/octet-stream"),
        )
