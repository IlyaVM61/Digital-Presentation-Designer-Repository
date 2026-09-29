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
) -> tuple[RenderedTable, list[Compensation]]:
    """Собрать таблицу и вернуть её вместе с применёнными компенсациями."""
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

    return (
        RenderedTable(
            headers=headers,
            rows=normalised,
            font=_font(tokens, slot),
            size_pt=_size(tokens, slot),
            header_color=_header_colour(tokens, slot),
            body_color=_body_colour(slot),
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


def _header_colour(tokens: DesignTokens | None, slot: Slot) -> str | None:
    """Шапка выделяется брендовым цветом, если он в шаблоне есть.

    Это единственная вольность, которую вёрстка себе позволяет, и она
    остаётся внутри палитры шаблона: цвет берётся из токенов, а не
    придумывается.
    """
    if tokens:
        brand = next(
            (token for token in tokens.colors if token.role == "brand.primary"), None
        )
        if brand:
            return brand.value
    return slot.text_style.color if slot.text_style else None


def _body_colour(slot: Slot) -> str | None:
    return slot.text_style.color if slot.text_style else None
