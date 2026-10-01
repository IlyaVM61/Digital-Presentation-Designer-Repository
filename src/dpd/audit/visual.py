"""Подслой 4c: аудит визуала через VLM по изображению слайда (T-52).

Изображения требуют два вопроса из всего набора: вопрос 6 Приложения 1 —
относятся ли картинки и пиктограммы к теме слайда — и визуальная
читаемость сверх набора ТЗ. Остальные решаются по тексту (4a) и по данным
файла (4b): читать текст с картинки, чтобы судить о его смысле, значит
добавить к задаче ошибки распознавания (`audit-architecture.md`).

**Один запрос на слайд, все вопросы разом**, в несколько потоков — как в
бюджете `models-strategy.md`.

**О читаемости модель не судит, а переписывает текст, который видит**, и
помечает строки, разбираемые с трудом. С тем, что положила вёрстка,
расшифровку сравнивает код (`checks/visual.py`): строки нет в расшифровке —
зритель её не увидит. Так решено по двум живым прогонам, где модель просили
судить. С вопросом «что здесь нечитаемо» она на светлом шаблоне пропустила
шапку таблицы цвета заливки, ответив пустыми списками за секунду, а на
тёмном выдумала перекрытый и обрезанный текст там, где всё читается.
Переписать видимое — задача, в которой VLM сильна, и проверяемая.

**Списка строк модель не видит** — иначе переписала бы его, а не
изображение, и невидимый текст «нашёлся» бы.

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

from pydantic import Field

from dpd.audit.registry import AuditContext, run_checks
from dpd.llm import ModelClient, PromptSet
from dpd.models import AuditReport, RenderedPresentation
from dpd.models.common import Contract
from dpd.models.rendered import RenderedElement

PROMPT = "skills/visual-auditor"
VISUAL_STAGE = "Проверка вида слайдов"
DEFAULT_WORKERS = 4


# --- Контракт ответа --------------------------------------------------------------


class SeenLine(Contract):
    """Строка, прочитанная на изображении, и как она читается."""

    text: str
    legibility: Literal["clear", "faint"]


class OffTopicPicture(Contract):
    """Картинка или пиктограмма, сюжет которой не связан с темой слайда."""

    shows: str = Field(min_length=1)
    why: str = Field(min_length=1)


class SlideLook(Contract):
    """Ответ модели об одном слайде."""

    lines: list[SeenLine]
    off_topic_pictures: list[OffTopicPicture]


@dataclass(frozen=True)
class VisualReview:
    """Ответы модели по слайдам варианта, в порядке слайдов."""

    slides: list[SlideLook]


def element_texts(element: RenderedElement) -> list[str]:
    """Строки, которые элемент выводит на слайд: текст, ячейки таблицы, подписи диаграммы.

    Цифры шкал диаграмма рисует сама, в данных колоды их нет — и сверять их не с чем.
    """
    if element.table is not None:
        return [*element.table.headers, *(cell for row in element.table.rows for cell in row)]
    if element.chart is not None:
        chart = element.chart
        titles = chart.axis_titles
        return [*chart.categories, *(series.name for series in chart.series), *filter(None, (titles.category, titles.value))]
    return [run.text for run in element.runs if run.text.strip()]


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

    def ask(number: int, image: Path) -> SlideLook:
        return client.complete(
            system,
            json.dumps({"number": number}),
            SlideLook,
            stage=VISUAL_STAGE,
            images=[Path(image).read_bytes()],
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(ask, number, image) for number, image in enumerate(images, start=1)]

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
    "SeenLine",
    "SlideLook",
    "VisualReview",
    "audit_visual",
    "element_texts",
    "review_visual",
]
