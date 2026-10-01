"""Содержание слайдов со ссылками на источник — генерация моделью (T-50).

По структуре колоды (T-49) модель пишет каждому слайду тело или
визуализацию и называет разделы фактуры, откуда взяты факты. Запрос — на
слайд, в несколько потоков: так заложено в бюджете `models-strategy.md`.
Модель видит бриф, всю фактуру, состав колоды и карточку своего слайда.

**Каждое число на слайде прослеживается до контент-пакета.** Фактура
делится на разделы по заголовкам Markdown, у каждого — якорь вида
`content.md#масштаб-пилота`. Ссылки на источник ограничены этими якорями
прямо в схеме ответа, и провайдер держит их при декодировании. Число,
которого нет в разделах, на которые слайд ссылается, — в заголовке,
ключевом сообщении, теле, ячейке таблицы или точке диаграммы — не проходит
проверку контракта, и клиент повторяет запрос с перечнем таких чисел
(T-47). Числа сравниваются без оглядки на запись: «1 240» и «1240», «4,5» и
«4.5» — одно число.

**Колода не падает из-за одного слайда** (T-61, вопрос T23). Найдено
приёмочным прогоном T-59: слайд, собирающий числа из нескольких разделов,
модель за три попытки не доводила до проверки — с каждым повтором
добавляла одну ссылку из нескольких нужных, — и `ModelError` одного слайда
ронял всю колоду. Поэтому ссылку на раздел, где число встречается одно,
ставит код: модель пишет, решает код (урок T-51 и T-52). Слайд, который за
все попытки так и не прошёл проверку, собирается из последнего ответа: число,
которого в фактуре нет, на слайд не попадает, а убранное называет проверка
текста. Ответ, из которого слайд не собрать, — модель не ответила или
ответила не по схеме — по-прежнему ошибка этапа (EF-8). ADR-0007.

**Ключевое сообщение на слайд отдельным элементом не выводится.** Оно —
тезис, который тело доказывает, и опора проверки «содержимое соответствует
заголовку» (T-51). Слота под подзаголовок схема шаблона не знает, а на
слайде с диаграммой или таблицей визуализация занимает место тела, так что
первый пункт тела показывал бы его не везде. Модель вправе уточнить
заголовок и ключевое сообщение — числа в них проверяются так же.
"""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Literal

from pydantic import Field, ValidationError, create_model, model_validator

from dpd.generation.content_pack import CONTENT_FILE, ContentPack
from dpd.generation.structure import DEFAULT_SLIDE_RANGE, generate_structure
from dpd.llm import ModelClient, ModelError, PromptSet
from dpd.llm.client import extract_json
from dpd.models import PresentationStructure, StructureSlide
from dpd.models.common import Contract
from dpd.models.structure import Omission, SlideBody, Visualization

PROMPT = "skills/slide-content-generator"
CONTENT_STAGE = "Содержание слайдов"

DEFAULT_WORKERS = 4
"""Запросов одновременно — бюджет `models-strategy.md`: 12 запросов в 4 потока."""

BODYLESS_ROLES = ("title", "cover")
"""Тело перевело бы титульный слайд на контентный макет (`requested_family`)."""

OPTIONAL_BODY_ROLES = (*BODYLESS_ROLES, "section", "divider")
"""Заставке достаточно названия раздела; тело делает её контентным слайдом."""

DECK_ROLES = ("agenda",)
"""Слайды, сделанные из состава колоды, а не из фактуры: ссылка им не нужна.
Найдено живым прогоном — у плана колоды модель ставила ссылку наугад."""

AXIS_FREE_CHARTS = ("pie",)
"""Круговой диаграмме оси не нужны — как в проверке `integrity.chart_no_labels`."""

HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t#]*$", re.MULTILINE)
NUMBER = re.compile(r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
GROUP_SEPARATORS = re.compile(r"[   ]")
LIST_NUMBER = re.compile(r"^[ \t]*\d+[.)][ \t]", re.MULTILINE)

NUMBER_WORDS = {
    1: "один одна одно одного одной одному одним одном",
    2: "два две двух двум двумя",
    3: "три трёх трех трём трем тремя",
    4: "четыре четырёх четырех четырём четырем четырьмя",
    5: "пять пяти пятью",
    6: "шесть шести шестью",
    7: "семь семи семью",
    8: "восемь восьми восемью",
    9: "девять девяти девятью",
    10: "десять десяти десятью",
}
"""Количественные числительные фактуры, которые на слайде можно записать
цифрой: «четыре квартала» и «4 квартала» — одно число. Порядковых здесь нет
нарочно: «каждый пятый» не равно «5», а тем более «20%»."""
WORD_NUMBERS = {word: str(value) for value, words in NUMBER_WORDS.items() for word in words.split()}
WORD = re.compile(r"\w+")

SCALES = {"тыс": 10**3, "млн": 10**6, "млрд": 10**9}
"""Множители фактуры: «2,8 млн ₽» на слайде можно записать как 2800000.
Найдено живым прогоном — точка диаграммы. Число без множителя рядом не
растягивается: иначе «5 этапов» пропустило бы выдуманные «5000»."""
SCALED = re.compile(rf"({NUMBER.pattern})[   ]*({'|'.join(SCALES)})(?!\w)")


# --- Разделы и числа фактуры ------------------------------------------------------


def fact_sections(content: str, file: str = CONTENT_FILE) -> dict[str, str]:
    """Фактура по разделам: якорь → текст раздела вместе с его заголовком.

    Раздел — от заголовка Markdown любого уровня до следующего. Якорь
    строится по правилам GitHub, чтобы ссылка открывала раздел в
    репозитории. Текст до первого заголовка и фактура без заголовков —
    раздел с якорем без решётки, по имени файла.
    """
    matches = list(HEADING.finditer(content))
    if not matches:
        return {file: content.strip()}

    sections: dict[str, str] = {}
    preamble = content[: matches[0].start()].strip()
    if preamble:
        sections[file] = preamble
    used: dict[str, int] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        slug = slugify(match.group(1))
        repeat = used.get(slug, 0)
        used[slug] = repeat + 1
        anchor = f"{file}#{slug}" if repeat == 0 else f"{file}#{slug}-{repeat}"
        sections[anchor] = content[match.start() : end].strip()
    return sections


def slugify(heading: str) -> str:
    """Якорь заголовка по правилам GitHub: строчные, без пунктуации, пробел — дефис."""
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", heading.lower()).strip())


def numbers_in(text: str) -> set[str]:
    """Числа текста в одной записи: без разделителя тысяч, с точкой в дроби."""
    return {canonical(raw) for raw in NUMBER.findall(text)}


def fact_numbers(section: str) -> set[str]:
    """Числа раздела фактуры: записанные цифрами и числительными до десяти.

    Номера пунктов нумерованного списка — не факты. Найдено живым прогоном:
    «4.» — номер этапа, и проверка, считавшая его числом, отправляла модель
    ссылаться не на тот раздел.
    """
    words = {WORD_NUMBERS[word] for word in WORD.findall(section.lower()) if word in WORD_NUMBERS}
    scaled = {
        number_text(float(Decimal(canonical(raw)) * SCALES[scale])) for raw, scale in SCALED.findall(section)
    }
    return numbers_in(LIST_NUMBER.sub("", section)) | words | scaled


def canonical(raw: str) -> str:
    return number_text(float(GROUP_SEPARATORS.sub("", raw).replace(",", ".")))


def number_text(value: float) -> str:
    return str(int(value)) if value.is_integer() else repr(value)


# --- Контракт ответа --------------------------------------------------------------


class SlideContent(Contract):
    """Ответ модели на один слайд.

    Ссылки на источник и проверку чисел добавляет `content_contract`: они
    зависят от разделов фактуры конкретного контент-пакета.
    """

    headline: str = Field(min_length=1)
    key_message: str = Field(min_length=1)
    body: SlideBody | None
    visualization: Visualization | None
    source_refs: list[str]


def content_contract(role: str, sections: dict[str, str]) -> type[SlideContent]:
    """Контракт ответа для слайда с ролью `role`: якоря в схеме, числа в проверке."""
    numbers = {anchor: fact_numbers(text) for anchor, text in sections.items()}

    def check(content: SlideContent) -> SlideContent:
        if role not in DECK_ROLES:
            content.source_refs = traced_refs(content, numbers)
        # Правила вне схемы: их держит не провайдер, а повтор клиента.
        problems = [
            *_shape_problems(content, role),
            *_untraced_numbers(content, numbers),
        ]
        if problems:
            raise ValueError("; ".join(problems))
        return content

    return create_model(
        "SlideContent",
        __base__=SlideContent,
        __validators__={"traced_to_facts": model_validator(mode="after")(check)},
        source_refs=(list[Literal[tuple(sections)]], ...),  # type: ignore[valid-type]
    )


def _shape_problems(content: SlideContent, role: str) -> list[str]:
    problems: list[str] = []
    visual = content.visualization
    has_body = bool(content.body and content.body.items)

    if role in BODYLESS_ROLES and (has_body or visual):
        problems.append(
            f"слайд с ролью {role} титульный: у него только заголовок, а тело или "
            "визуализация перевели бы его на контентный макет"
        )
    elif role not in OPTIONAL_BODY_ROLES and not (has_body or visual):
        problems.append(f"слайд с ролью {role} пуст: ему нужно тело или визуализация")
    if has_body and visual:
        problems.append("у слайда и тело, и визуализация: визуализация занимает место тела, и текст тела пропал бы")
    if visual:
        problems.extend(_visual_problems(visual))
    if (has_body or visual) and not content.source_refs and role not in DECK_ROLES:
        problems.append("sourceRefs пуст: слайду с телом или визуализацией нужна ссылка на раздел фактуры")
    return problems


def _visual_problems(visual: Visualization) -> list[str]:
    if visual.kind == "chart":
        chart = visual.chart
        if chart is None or visual.table is not None:
            return ["визуализация вида chart несёт данные в поле chart, а поле table пусто"]
        if not chart.categories or not chart.series:
            return ["у диаграммы нет категорий или рядов данных"]
        problems = [
            f"в ряду «{series.name}» точек {len(series.points)}, а категорий {len(chart.categories)}"
            for series in chart.series
            if len(series.points) != len(chart.categories)
        ]
        axes = chart.axis_titles
        if chart.chart_type not in AXIS_FREE_CHARTS and not (axes.category and axes.value):
            problems.append(f"у диаграммы {chart.chart_type} не подписаны оси: нужны axisTitles.category и axisTitles.value")
        return problems

    table = visual.table
    if table is None or visual.chart is not None:
        return ["визуализация вида table несёт данные в поле table, а поле chart пусто"]
    if not table.headers or not table.rows:
        return ["у таблицы нет шапки или строк"]
    return [
        f"столбцов в строке {number} таблицы {len(row)}, а в шапке {len(table.headers)}"
        for number, row in enumerate(table.rows, start=1)
        if len(row) != len(table.headers)
    ]


def traced_refs(content: SlideContent, numbers: dict[str, set[str]]) -> list[str]:
    """Ссылки слайда и разделы, где число слайда встречается одно (T-61).

    Число, которое есть ровно в одном разделе фактуры, взято оттуда, и
    повторять запрос ради ссылки незачем. Число из нескольких разделов код ни
    к одному не относит: откуда оно взято, знает только модель. Плану колоды
    (`DECK_ROLES`) ссылки не ставятся: его числа — части колоды, и совпадение
    с фактурой у них случайно.
    """
    refs = list(content.source_refs)
    for _, text in slide_texts(content):
        for raw in NUMBER.findall(text):
            holders = [anchor for anchor, found in numbers.items() if canonical(raw) in found]
            if len(holders) == 1 and holders[0] not in refs:
                refs.append(holders[0])
    return refs


def _untraced_numbers(content: SlideContent, numbers: dict[str, set[str]]) -> list[str]:
    """Числа слайда, которых нет в разделах, на которые он ссылается."""
    cited = set().union(*(numbers[anchor] for anchor in content.source_refs))
    problems: list[str] = []
    for where, text in slide_texts(content):
        for raw in NUMBER.findall(text):
            number = canonical(raw)
            if number in cited:
                continue
            holders = [anchor for anchor, found in numbers.items() if number in found]
            if holders:
                # Совпадение бывает случайным — «5 частей» у плана колоды, —
                # поэтому выходов два, а не один: подсказка только про ссылку
                # загоняла модель в рассуждение до лимита токенов.
                problems.append(
                    f"{where}: число {raw} есть в разделах {', '.join(holders)}, а их нет в sourceRefs — "
                    "нужна ссылка на раздел, если число взято оттуда, или формулировка без числа"
                )
            else:
                problems.append(
                    f"{where}: числа {raw} нет в фактуре — числа переносятся из неё как есть, "
                    "без пересчёта, а записанное в фактуре словами остаётся словами"
                )
    return problems


def slide_texts(content: SlideContent | StructureSlide) -> list[tuple[str, str]]:
    """Всё, что увидит зритель или проверит аудит, с путём к полю в ответе.

    Принимает и ответ модели, и слайд замысла: аудит текста (T-51) сверяет
    числа тем же обходом, что и контракт генерации, — иначе они разошлись бы
    в том, что считать текстом слайда.
    """
    texts = [("headline", content.headline), ("keyMessage", content.key_message or "")]
    if content.body:
        texts += [(f"body.items[{n}]", item) for n, item in enumerate(content.body.items)]

    if content.visualization:
        texts += visual_texts(content.visualization)
    return texts


def visual_texts(visual: Visualization) -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []
    if visual.table:
        table = visual.table
        texts += [(f"table.headers[{n}]", header) for n, header in enumerate(table.headers)]
        texts += [
            (f"table.rows[{r}][{c}]", cell) for r, row in enumerate(table.rows) for c, cell in enumerate(row)
        ]
    if visual.chart:
        chart = visual.chart
        texts += [(f"chart.categories[{n}]", category) for n, category in enumerate(chart.categories)]
        for s, series in enumerate(chart.series):
            texts.append((f"chart.series[{s}].name", series.name))
            texts += [(f"chart.series[{s}].points[{p}]", number_text(point)) for p, point in enumerate(series.points)]
        texts += [(f"chart.axisTitles.{axis}", title) for axis, title in chart.axis_titles if title]
    return texts


# --- Слайд из неудачного ответа ---------------------------------------------------


def salvage(answer: str, slide: StructureSlide, numbers: dict[str, set[str]]) -> StructureSlide | None:
    """Слайд из последнего ответа, не прошедшего проверку (T-61, вопрос T23).

    Число, которого в фактуре нет, на слайд не попадает: пункт с ним убран и
    назван в `omitted`, визуализация с ним убрана целиком — диаграмма без
    точки или таблица без ячейки врали бы сильнее, чем слайд без них, —
    заголовок и ключевое сообщение остаются из структуры колоды. Число,
    которое в фактуре есть, но в нескольких разделах и ни в одном из тех, на
    что слайд ссылается, остаётся: то ли это число, решает человек по
    предупреждению проверки текста (`content.unsourced_numbers`).

    Форма слайда — та, что ждёт вёрстка: у титула нет тела, несобранная
    визуализация не показывается, а из тела и визуализации остаётся
    визуализация — так их и свёрстали бы. Слайд, на котором после этого не
    осталось ничего, кроме заголовка, называет проверка пустого слайда.

    Ответ, который не разбирается как ответ на слайд, сохранить нельзя — `None`.
    """
    try:
        content = SlideContent.model_validate_json(extract_json(answer))
    except ValidationError:
        return None

    content.source_refs = [anchor for anchor in content.source_refs if anchor in numbers]
    if slide.role not in DECK_ROLES:
        content.source_refs = traced_refs(content, numbers)
    known = set().union(*numbers.values())

    def invented(text: str) -> list[str]:
        return list(dict.fromkeys(number for raw in NUMBER.findall(text) if (number := canonical(raw)) not in known))

    omitted: list[Omission] = []
    items: list[str] = []
    for item in content.body.items if content.body else []:
        if found := invented(item):
            omitted.append(Omission(text=item, numbers=found))
        else:
            items.append(item)

    visual = content.visualization
    if visual and _visual_problems(visual):
        visual = None
    if visual and (found := invented(" ".join(text for _, text in visual_texts(visual)))):
        omitted.append(Omission(text=_visual_label(visual), numbers=found))
        visual = None

    body = content.body.model_copy(update={"items": items}) if content.body and items else None
    if slide.role in BODYLESS_ROLES:
        body, visual = None, None
    elif visual:
        body = None

    return StructureSlide(
        id=slide.id,
        role=slide.role,
        headline=slide.headline if invented(content.headline) else content.headline,
        key_message=slide.key_message if invented(content.key_message) else content.key_message,
        body=body,
        visualization=visual,
        source_refs=content.source_refs,
        omitted=omitted,
    )


def _visual_label(visual: Visualization) -> str:
    """Как назвать убранную визуализацию человеку: по шапке или по рядам."""
    if visual.table:
        return f"таблица: {', '.join(visual.table.headers)}"
    names = ", ".join(series.name for series in visual.chart.series) if visual.chart else ""
    return f"диаграмма: {names}"


# --- Генерация --------------------------------------------------------------------


def generate_content(
    structure: PresentationStructure,
    pack: ContentPack,
    client: ModelClient,
    prompts: PromptSet,
    *,
    workers: int = DEFAULT_WORKERS,
) -> PresentationStructure:
    """Тело или визуализация и ссылки на источник для каждого слайда структуры.

    Ответ, не прошедший проверку за все попытки, не роняет колоду: слайд
    собирается из него без выдуманных чисел (`salvage`, T-61). Ответ, из
    которого слайд не собрать, поднимает `ModelError` с этапом
    `CONTENT_STAGE` (EF-8). После такого сбоя слайды, до которых очередь не
    дошла, не запрашиваются: их ответы всё равно были бы выброшены.
    """
    sections = fact_sections(pack.content)
    numbers = {anchor: fact_numbers(text) for anchor, text in sections.items()}
    system = prompts.get(PROMPT).text
    facts = "\n\n".join(f"[{anchor}]\n{text}" for anchor, text in sections.items())
    deck = [
        {"number": number, "role": slide.role, "headline": slide.headline}
        for number, slide in enumerate(structure.slides, start=1)
    ]
    failed = threading.Event()

    def write(number: int, slide: StructureSlide) -> StructureSlide | None:
        if failed.is_set():
            return None
        card = {"number": number, "role": slide.role, "headline": slide.headline, "keyMessage": slide.key_message}
        user = f"{pack.brief}\n\n{facts}\n\n{json.dumps({'deck': deck, 'slide': card}, ensure_ascii=False)}"
        try:
            content = client.complete(system, user, content_contract(slide.role, sections), stage=CONTENT_STAGE)
        except ModelError as error:
            salvaged = salvage(error.answer, slide, numbers) if error.answer is not None else None
            if salvaged is None:
                failed.set()
                raise
            return salvaged
        except BaseException:
            failed.set()
            raise
        return StructureSlide(
            id=slide.id,
            role=slide.role,
            headline=content.headline,
            key_message=content.key_message,
            body=content.body if content.body and content.body.items else None,
            visualization=content.visualization,
            source_refs=list(content.source_refs),
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(write, number, slide) for number, slide in enumerate(structure.slides, start=1)]

    errors = [error for future in futures if (error := future.exception()) is not None]
    if errors:
        raise errors[0]
    return structure.model_copy(update={"slides": [future.result() for future in futures]})


def generate_presentation(
    pack: ContentPack,
    client: ModelClient,
    prompts: PromptSet,
    *,
    slide_range: tuple[int, int] = DEFAULT_SLIDE_RANGE,
    workers: int = DEFAULT_WORKERS,
) -> PresentationStructure:
    """Колода по контент-пакету: структура (T-49), затем содержание слайдов."""
    structure = generate_structure(pack, client, prompts, slide_range=slide_range)
    return generate_content(structure, pack, client, prompts, workers=workers)
