"""Подслой 4c: аудит визуала через VLM по изображению слайда (T-52).

Изображения требуют два вопроса из всего набора: вопрос 6 Приложения 1 —
относятся ли картинки и пиктограммы к теме слайда — и визуальная
читаемость сверх набора ТЗ. Остальные решаются по тексту (4a) и по данным
файла (4b): читать текст с картинки, чтобы судить о его смысле, значит
добавить к задаче ошибки распознавания (`audit-architecture.md`).

**Один запрос на слайд, все вопросы разом**, в несколько потоков — как в
бюджете `models-strategy.md`. Модель видит изображение и список того, что на
слайд положила вёрстка: текст, который есть в списке, но не виден на
изображении, без списка не заметить. Замечание о читаемости называет номер
элемента — перечисление в схеме ответа, — и находка получает его блок:
подсветка покажет место, а не весь слайд.

**Смотрится вариант, который выбран, — после выбора** (ADR-0002, п. 4).
С T-39 вариант выбирают, посмотрев на все три, и прогон модель не зовёт:
три варианта стоили бы втрое больше запросов при том, что экспортируют
один. Колоды сдачи — все три варианта сразу, выбора там нет, и батч
смотрит каждую: это и есть «полный прогон по явному требованию».

**Без модели проверки не молчат**, а названы в отчёте пропущенными: пустой
список находок неотличим от чистого слайда.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, create_model

from dpd.audit.registry import AuditContext, run_checks
from dpd.llm import ModelClient, PromptSet
from dpd.models import AuditReport, RenderedPresentation
from dpd.models.common import Contract
from dpd.models.rendered import RenderedElement, Slide

PROMPT = "skills/visual-auditor"
VISUAL_STAGE = "Проверка вида слайдов"
DEFAULT_WORKERS = 4


# --- Контракт ответа --------------------------------------------------------------


class Unreadable(Contract):
    """Элемент слайда, который трудно прочитать или который выглядит случайным."""

    element: int
    problem: str = Field(min_length=1)


class OffTopicPicture(Contract):
    """Картинка или пиктограмма, сюжет которой не связан с темой слайда."""

    shows: str = Field(min_length=1)
    why: str = Field(min_length=1)


class SlideLook(Contract):
    """Ответ модели об одном слайде: пустые списки — замечаний нет.

    Списки, а не «да/нет»: в аудите текста модель путала, что значит
    `ok: false` (T-51), а перечислить увиденное ей проще.
    """

    readability: list[Unreadable]
    off_topic_pictures: list[OffTopicPicture]


@dataclass(frozen=True)
class VisualReview:
    """Ответы модели по слайдам варианта, в порядке слайдов."""

    slides: list[SlideLook]


def look_contract(slide: Slide) -> type[SlideLook]:
    """Контракт ответа о слайде: номер элемента — перечисление в схеме.

    Провайдер держит схему при декодировании (T-47), и замечание не укажет на
    блок, которого на слайде нет. У слайда без содержимого читать нечего.
    """
    numbers = tuple(range(1, len(slide.elements) + 1))
    if not numbers:
        return create_model(  # type: ignore[call-overload]
            "SlideLook", __base__=SlideLook, readability=(list[Unreadable], Field(max_length=0))
        )
    unreadable = create_model("Unreadable", __base__=Unreadable, element=(Literal[numbers], ...))  # type: ignore[valid-type]
    return create_model("SlideLook", __base__=SlideLook, readability=(list[unreadable], ...))  # type: ignore[valid-type]


def elements_card(slide: Slide) -> list[dict]:
    """Что вёрстка положила на слайд — то, с чем модель сравнивает изображение."""
    return [_element(number, element) for number, element in enumerate(slide.elements, start=1)]


def _element(number: int, element: RenderedElement) -> dict:
    item: dict = {"n": number, "kind": element.kind}
    if element.table is not None:
        item["headers"] = list(element.table.headers)
        item["rows"] = [list(row) for row in element.table.rows]
    elif element.chart is not None:
        chart = element.chart
        item["categories"] = list(chart.categories)
        item["series"] = [series.name for series in chart.series]
        item["axisTitles"] = chart.axis_titles.model_dump(by_alias=True, exclude_none=True)
    else:
        item["text"] = [run.text for run in element.runs if run.text.strip()]
    return item


# --- Запрос к модели ----------------------------------------------------------------


def review_visual(
    deck: RenderedPresentation,
    images: Sequence[Path],
    client: ModelClient,
    prompts: PromptSet,
    *,
    workers: int = DEFAULT_WORKERS,
) -> VisualReview:
    """Ответы модели по изображению каждого слайда — запрос на слайд.

    Невалидный после всех попыток ответ о любом слайде поднимает `ModelError`
    с этапом `VISUAL_STAGE`: аудит, проверивший часть слайдов, выдал бы
    непросмотренные за чистые.
    """
    if len(images) != len(deck.slides):
        raise ValueError(
            f"изображений {len(images)}, а слайдов {len(deck.slides)}: "
            "аудит визуала смотрит на рендер именно этой колоды"
        )
    system = prompts.get(PROMPT).text

    def ask(number: int, slide: Slide, image: Path) -> SlideLook:
        card = {"number": number, "elements": elements_card(slide)}
        return client.complete(
            system,
            json.dumps(card, ensure_ascii=False),
            look_contract(slide),
            stage=VISUAL_STAGE,
            images=[Path(image).read_bytes()],
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(ask, number, slide, image)
            for number, (slide, image) in enumerate(zip(deck.slides, images, strict=True), start=1)
        ]

    errors = [error for future in futures if (error := future.exception()) is not None]
    if errors:
        raise errors[0]
    return VisualReview(slides=[future.result() for future in futures])


# --- Подслой целиком ----------------------------------------------------------------


def audit_visual(
    deck: RenderedPresentation,
    images: Sequence[Path] | None = None,
    client: ModelClient | None = None,
    prompts: PromptSet | None = None,
    *,
    workers: int = DEFAULT_WORKERS,
    run_id: str | None = None,
) -> AuditReport:
    """Выполнить проверки подслоя 4c для одного варианта.

    Без клиента модели проверки названы пропущенными — так прогон отмечает
    варианты, на которые модель ещё не смотрела. Отчёт дописывается в отчёт
    варианта через `dpd.audit.textual.attach`.
    """
    review = None
    if client is not None:
        if images is None or prompts is None:
            raise ValueError("аудит визуала смотрит на изображения слайдов: нужны изображения и промпты")
        review = review_visual(deck, images, client, prompts, workers=workers)

    context = AuditContext(deck=deck, visual_review=review)
    return run_checks(context, run_id=run_id, sublayers=("4c",))


__all__ = [
    "PROMPT",
    "VISUAL_STAGE",
    "OffTopicPicture",
    "SlideLook",
    "Unreadable",
    "VisualReview",
    "audit_visual",
    "elements_card",
    "look_contract",
    "review_visual",
]
