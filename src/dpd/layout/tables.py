"""Сборка таблиц по дизайн-токенам шаблона.

**Оформление синтезируется, а не копируется.** В 138 слайдах-примерах трёх
калибровочных шаблонов всего четыре таблицы, и извлекать образец не из чего.
Поэтому гарнитура, кегль и цвета берутся из токенов — тех же, что применяются
к тексту, — и таблица выглядит частью той же дизайн-системы.

**Размер ограничен 7×5** по чек-листу аудита: таблица крупнее перестаёт
читаться со слайда. Лишнее не выбрасывается молча — сокращение фиксируется
компенсацией, как и переполнение текста.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

from dpd.layout.contrast import readable
from dpd.models import Compensation, DesignTokens, RenderedTable, Slot, TableSpec

MAX_ROWS = 7
"""Строк вместе с шапкой. Порог из чек-листа аудита."""

MAX_COLUMNS = 5

TABLE_SIZE_STEPS_DOWN = 2
"""На сколько ступеней шкалы кегль таблицы мельче основного текста.

Таблица плотнее абзаца: тот же кегль сделал бы её нечитаемой сеткой.
"""


def build(
    spec: TableSpec,
    slot: Slot,
    tokens: DesignTokens | None,
    background: str | None = None,
) -> tuple[RenderedTable, list[Compensation]]:
    """Собрать таблицу и вернуть её вместе с применёнными компенсациями.

    `background` — цвет фона макета, если он вычислим; под фоном-изображением
    он неизвестен, и цвет строк остаётся тем, которым шаблон набирает слот.
    """
    headers = spec.headers[:MAX_COLUMNS]
    rows = [row[: len(headers)] for row in spec.rows[: MAX_ROWS - 1]]

    compensations: list[Compensation] = []
    dropped_rows = len(spec.rows) - len(rows)
    dropped_columns = len(spec.headers) - len(headers)
    if dropped_rows > 0 or dropped_columns > 0:
        compensations.append(
            Compensation(
                kind="tableTrim",
                slot_id=slot.id,
                from_value=float(len(spec.rows) * max(len(spec.headers), 1)),
                to_value=float(len(rows) * max(len(headers), 1)),
                reason=(
                    f"таблица сокращена до {MAX_ROWS}×{MAX_COLUMNS}: "
                    f"отброшено строк — {max(dropped_rows, 0)}, "
                    f"колонок — {max(dropped_columns, 0)}"
                ),
            )
        )

    # Строки выравниваются по ширине шапки: неровная сетка ломает экспорт.
    normalised = [row + [""] * (len(headers) - len(row)) for row in rows]
    body_colour = _body_colour(slot, tokens, background)
    header_fill, header_colour = _header(tokens, body_colour)

    return (
        RenderedTable(
            headers=headers,
            rows=normalised,
            font=_font(tokens, slot),
            size_pt=_size(tokens, slot),
            header_color=header_colour,
            header_fill=header_fill,
            body_color=body_colour,
        ),
        compensations,
    )


def _font(tokens: DesignTokens | None, slot: Slot) -> str | None:
    if tokens and tokens.fonts:
        return tokens.fonts[0].family
    return slot.text_style.font if slot.text_style else None


def _size(tokens: DesignTokens | None, slot: Slot) -> float | None:
    base = slot.text_style.size_pt if slot.text_style else None
    scale = tokens.type_scale.values if tokens else []
    if base is None or base not in scale:
        return base
    index = max(scale.index(base) - TABLE_SIZE_STEPS_DOWN, 0)
    return scale[index]


def _header(tokens: DesignTokens | None, body: str | None) -> tuple[str | None, str | None]:
    """Заливка шапки и цвет её текста — одной парой (T-56).

    Шапка заливается брендовым цветом, если он в шаблоне есть: это
    единственная вольность, которую вёрстка себе позволяет, и она остаётся
    внутри палитры. Текст на заливке — первый читаемый на ней цвет: сначала
    цвет строк, чтобы таблица была набрана одним цветом, потом цвета палитры
    по частоте. Цвет не из палитры не берётся: аудит заменил бы его ближайшим
    из палитры, не глядя на заливку.

    Раньше заливку давал стиль таблицы по умолчанию — цвет акцента темы, а
    текст красился брендовым: где они совпадали, шапка исчезала. Если ни один
    цвет палитры на брендовом не читается, шапка остаётся без заливки и
    набирается, как строки.
    """
    brand = next(
        (token.value for token in tokens.colors if token.role == "brand.primary"), None
    ) if tokens else None
    if brand is None:
        return None, body

    palette = [token.value for token in tokens.colors]
    text = readable([colour for colour in [body, *palette] if colour != brand], brand)
    if text is None:
        return None, body
    return brand, text


def _body_colour(slot: Slot, tokens: DesignTokens | None, background: str | None) -> str | None:
    """Цвет строк: у них нет заливки, и они лежат прямо на фоне слайда.

    Берётся цвет, которым шаблон набирает слот, — если он читается на фоне
    мелким кеглем таблицы. Синий VK на белом даёт 4,13:1: основному тексту
    крупного кегля этого хватает, таблице — нет, и тогда берётся первый
    читаемый цвет палитры. Не читается ничего — остаётся цвет слота.
    """
    own = slot.text_style.color if slot.text_style else None
    palette = [token.value for token in tokens.colors] if tokens else []
    return readable([own, *palette], background) or own
