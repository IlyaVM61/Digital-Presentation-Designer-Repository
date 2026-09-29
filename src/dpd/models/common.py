"""Типы, общие для контрактов между слоями.

Все контракты наследуют `Contract`: он задаёт camelCase в JSON, потому что
нормативные документы `template-schema.md` и `pipeline-architecture.md`
описаны в camelCase, и по этим же моделям генерируется JSON Schema для
валидации ответов моделей. Python-код при этом оперирует snake_case.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

HEX_COLOR = r"^#[0-9A-Fa-f]{6}$"


class Contract(BaseModel):
    """База контрактов: camelCase наружу, snake_case внутри, лишних полей нет."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class Bounds(Contract):
    """Прямоугольник в долях холста.

    Абсолютные EMU в схеме не хранятся нигде, кроме `Canvas`: холсты шаблонов
    различаются, и пороги аудита, откалиброванные на одном, оказались бы
    неверными на другом. Пересчёт в EMU выполняет экспортёр.

    Каждая координата лежит в [0, 1] по отдельности, но сумма не проверяется:
    элемент, вылезающий за правый край, — дефект вёрстки, который проверка
    аудита должна увидеть, а не невалидный контракт, который до неё не дойдёт.
    """

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(ge=0, le=1)
    h: float = Field(ge=0, le=1)


class Canvas(Contract):
    """Фактический размер холста шаблона. Единственное место с абсолютными EMU."""

    width_emu: int = Field(gt=0)
    height_emu: int = Field(gt=0)


class TextRun(Contract):
    """Отрезок текста с единым оформлением.

    Все свойства оформления необязательны: до 59% текстовых прогонов реальных
    шаблонов не несут явного кегля и шрифта, и требовать их здесь значило бы
    воспроизвести отказ, на котором падает presenton. Незаполненное свойство
    разрешается по цепочке наследования при вёрстке.
    """

    text: str
    font: str | None = None
    size_pt: float | None = Field(default=None, gt=0)
    color: str | None = Field(default=None, pattern=HEX_COLOR)
    bold: bool = False
