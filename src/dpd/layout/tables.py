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

HEADER_MIN_CONTRAST = 4.5
"""Контраст текста шапки к её заливке — порог WCAG AA для обычного текста,
тот же, что у проверки `template.contrast_low`. Кегль таблицы мельче
основного текста, и сниженный порог крупного текста к ней не относится."""

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
    header_fill, header_colour = _header(tokens, slot)

    return (
        RenderedTable(
            headers=headers,
            rows=normalised,
            font=_font(tokens, slot),
            size_pt=_size(tokens, slot),
            header_color=header_colour,
            header_fill=header_fill,
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


def _header(tokens: DesignTokens | None, slot: Slot) -> tuple[str | None, str | None]:
    """Заливка шапки и цвет её текста — одной парой (T-56).

    Шапка заливается брендовым цветом, если он в шаблоне есть: это
    единственная вольность, которую вёрстка себе позволяет, и она остаётся
    внутри палитры. Текст на заливке — первый читаемый на ней цвет: сначала
    цвет строк, чтобы таблица была набрана одним цветом, потом цвета палитры
    по частоте. Цвет не из палитры не берётся — он выдал бы, что слайд собран
    мимо шаблона, а аудит заменил бы его ближайшим из палитры, не глядя на
    заливку.

    Раньше заливку давал стиль таблицы по умолчанию — цвет акцента темы, а
    текст красился брендовым: где они совпадали, шапка исчезала. Если ни один
    цвет палитры на брендовом не читается, шапка остаётся без заливки и
    набирается, как строки.
    """
    body = _body_colour(slot)
    brand = next(
        (token.value for token in tokens.colors if token.role == "brand.primary"), None
    ) if tokens else None
    if brand is None:
        return None, body

    palette = [token.value for token in tokens.colors]
    candidates = [colour for colour in [body, *palette] if colour and colour != brand]
    readable = next(
        (colour for colour in candidates if _contrast(colour, brand) >= HEADER_MIN_CONTRAST), None
    )
    if readable is None:
        return None, body
    return brand, readable


def _body_colour(slot: Slot) -> str | None:
    """Строки без заливки лежат на фоне слайда — и набираются, как основной текст."""
    return slot.text_style.color if slot.text_style else None


def _contrast(first: str, second: str) -> float:
    """Отношение контраста по WCAG: (светлее + 0,05) / (темнее + 0,05)."""
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _luminance(colour: str) -> float:
    channels = [int(colour[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
