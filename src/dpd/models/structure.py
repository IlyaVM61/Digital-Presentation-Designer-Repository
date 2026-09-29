"""`PresentationStructure` — выход слоя генерации и вход вёрстки.

**Минимальный срез задачи T-05/T-07.** Нормативное описание в
`docs/04-architecture/pipeline-architecture.md` шире: `visualization` с
диаграммами и таблицами (T-23, T-24) появится своей задачей.

**Ключевое свойство контракта:** здесь нет ни одной координаты, ни одного
кода цвета, ни одного названия шрифта и ни одного идентификатора макета.
Структура описывает, что сказать и чем это показать, но не как это выглядит.
По этой границе проходит раздел ответственности между моделью и
детерминированным движком, и на нём держится воспроизводимость.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dpd.models.common import Contract

BodyKind = Literal["bullets", "paragraphs"]


class StructureMeta(Contract):
    """Свойства колоды целиком."""

    language: str = "ru"
    purpose: str | None = None
    audience: str | None = None


class SlideBody(Contract):
    """Содержание слайда без оформления."""

    kind: BodyKind = "bullets"
    items: list[str] = Field(default_factory=list)


class TableSpec(Contract):
    """Табличные данные: шапка и строки.

    Ограничение размера накладывает вёрстка, а не замысел: сколько строк
    поместится, зависит от слота и кегля, а не от содержания.
    """

    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class Visualization(Contract):
    """Чем показать содержимое слайда.

    Тип визуализации выбирает модель — это вопрос смысла. Оформление
    синтезирует вёрстка из дизайн-токенов: в 138 слайдах-примерах трёх
    калибровочных шаблонов всего четыре таблицы и ни одной диаграммы,
    и копировать оформление не из чего.
    """

    kind: Literal["table", "chart"]
    table: TableSpec | None = None


class StructureSlide(Contract):
    """Слайд как замысел: что сказать и в какой роли.

    `source_refs` пока необязательно. С задачи T-50 оно станет обязательным:
    на нём держится проверка «все цифры и факты со слайда есть в исходных
    материалах», иначе её пришлось бы выполнять поиском по тексту.
    """

    id: str
    role: str
    headline: str
    key_message: str | None = None
    body: SlideBody | None = None
    visualization: Visualization | None = None
    source_refs: list[str] = Field(default_factory=list)


class PresentationStructure(Contract):
    """Замысел колоды: состав слайдов, роли, тексты."""

    meta: StructureMeta = Field(default_factory=StructureMeta)
    slides: list[StructureSlide] = Field(min_length=1)
