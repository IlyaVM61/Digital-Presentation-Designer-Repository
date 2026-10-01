"""Структура колоды по брифу — генерация моделью (T-49).

Модель читает бриф и фактуру контент-пакета и одним запросом решает, из
каких слайдов состоит колода и в каком порядке они идут: роль, заголовок и
ключевое сообщение каждого. Это вопрос смысла, поэтому его решает модель;
макет по роли выбирает уже детерминированная вёрстка.

Один запрос на колоду, а не на слайд, — так заложено в бюджете
`models-strategy.md`: последовательность видна только целиком. Тело слайда,
визуализация и ссылки на источник — следующий шаг, генерация содержания
(T-50); структура их не выдумывает.

Объём колоды записан в схему ответа (`minItems`/`maxItems`): провайдер
держит его при декодировании, а ответ, всё же вышедший за рамки, клиент
повторяет с перечнем ошибок (T-47).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, create_model, model_validator

from dpd.generation.content_pack import ContentPack
from dpd.llm import ModelClient, PromptSet
from dpd.models import PresentationStructure, StructureSlide
from dpd.models.common import Contract
from dpd.models.structure import SlideRole, StructureMeta

PROMPT = "skills/outline-generator"
STRUCTURE_STAGE = "Структура колоды"

DEFAULT_SLIDE_RANGE = (10, 15)
"""Объём колоды по умолчанию — FR-13 по разделу ТЗ «Рамки решения».
Пользователь вправе задать свой; тогда он заменяет этот диапазон целиком."""

OPENING_ROLES = ("title", "cover")


class OutlineMeta(Contract):
    """Что модель поняла из брифа: назначение (FR-12), аудитория, язык."""

    purpose: Literal["feature", "product", "project", "initiative"]
    audience: str = Field(min_length=1)
    language: str = Field(min_length=2, max_length=2)


class OutlineSlide(Contract):
    """Слайд как замысел: зачем он в колоде и что зритель должен из него унести."""

    role: SlideRole
    headline: str = Field(min_length=1)
    key_message: str = Field(min_length=1)


class DeckOutline(Contract):
    """Ответ модели. Объём задаётся подклассом из `outline_contract`."""

    meta: OutlineMeta
    slides: list[OutlineSlide]

    @model_validator(mode="after")
    def opens_with_title(self) -> DeckOutline:
        # Правило вне схемы: его держит не провайдер, а повтор клиента.
        if self.slides and self.slides[0].role not in OPENING_ROLES:
            raise ValueError(
                f"первый слайд колоды — титульный: роль {' или '.join(OPENING_ROLES)}, "
                f"а не {self.slides[0].role}"
            )
        return self


def outline_contract(low: int, high: int) -> type[DeckOutline]:
    """Контракт ответа с объёмом колоды в схеме."""
    return create_model(
        "DeckOutline",
        __base__=DeckOutline,
        slides=(list[OutlineSlide], Field(min_length=low, max_length=high)),
    )


def generate_structure(
    pack: ContentPack,
    client: ModelClient,
    prompts: PromptSet,
    *,
    slide_range: tuple[int, int] = DEFAULT_SLIDE_RANGE,
) -> PresentationStructure:
    """Состав колоды по брифу: роль, заголовок, ключевое сообщение каждого слайда.

    Невалидный после всех попыток ответ поднимает `ModelError` с этапом
    `STRUCTURE_STAGE` — частичной структуры дальше по пайплайну не уходит (EF-8).
    """
    low, high = slide_range
    if low < 1 or low > high:
        raise ValueError(f"объём колоды задан неверно: от {low} до {high} слайдов")

    system = prompts.get(PROMPT).render(min_slides=str(low), max_slides=str(high))
    user = f"{pack.brief}\n\n{pack.content}"
    outline = client.complete(system, user, outline_contract(low, high), stage=STRUCTURE_STAGE)

    return PresentationStructure(
        meta=StructureMeta(
            language=outline.meta.language,
            purpose=outline.meta.purpose,
            audience=outline.meta.audience,
        ),
        slides=[
            StructureSlide(
                id=f"s{number}",
                role=slide.role,
                headline=slide.headline,
                key_message=slide.key_message,
            )
            for number, slide in enumerate(outline.slides, start=1)
        ],
    )
