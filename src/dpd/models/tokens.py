"""Дизайн-токены шаблона: гарнитуры, цвета, типографическая шкала.

**Токены извлекаются из фактической разметки, а не из темы.** Во всех четырёх
проверенных шаблонах `theme1.xml` расходится с оформлением: объявляет Arial,
тогда как размечено Play, а в одном шаблоне тема несёт дефолтную палитру
Office вместо брендовой. Тема — лишь один из сигналов, и расхождение с ней
фиксируется в `conflicts_with` как свойство шаблона.

**Минимальный срез задачи T-12.** Очистка шкалы от кеглей, порождённых
автоподгонкой, — задача T-16; поля отступов — позже.
"""

from __future__ import annotations

from pydantic import Field

from dpd.models.common import HEX_COLOR, Contract


class Token(Contract):
    """Общее у токенов: откуда взято, сколько раз встретилось, насколько верим.

    `sources` — перечень сигналов, подтвердивших значение, в порядке убывания
    веса. `confidence` аудит использует как порог: находка, опирающаяся на
    токен с низкой уверенностью, показывается как рекомендация, а не как
    нарушение.
    """

    role: str
    sources: list[str] = Field(default_factory=list)
    occurrences: int = 0
    confidence: float = Field(default=0.0, ge=0, le=1)


class ColorToken(Token):
    """Цвет шаблона."""

    value: str = Field(pattern=HEX_COLOR)


class FontToken(Token):
    """Гарнитура шаблона.

    `conflicts_with` фиксирует расхождение между источниками — например,
    между фактической разметкой и темой. Это не ошибка разбора, а свойство
    файла, о котором пользователь должен знать.
    """

    family: str
    conflicts_with: dict[str, str] = Field(default_factory=dict)
    embedded: bool = False


class TypeScale(Contract):
    """Типографическая шкала шаблона.

    `excluded` наполняется задачей T-16: дробные кегли вида 8.12 или 6.75
    порождены автоподгонкой текста, а не решением дизайнера, и принимать их
    за шкалу нельзя.
    """

    values: list[float] = Field(default_factory=list)
    excluded: list[float] = Field(default_factory=list)
    exclusion_reason: str | None = None


class DesignTokens(Contract):
    """Правила оформления, извлечённые из шаблона."""

    fonts: list[FontToken] = Field(default_factory=list)
    colors: list[ColorToken] = Field(default_factory=list)
    type_scale: TypeScale = Field(default_factory=TypeScale)
