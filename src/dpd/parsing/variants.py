"""Цветовая схема макета и группировка макетов в вариации.

Цветовые вариации одного макета — готовый механизм визуального различения
вариантов колоды, не выходящий за правила шаблона: берутся родные макеты.
Но единственной осью различий он быть не может — в одном калибровочном
шаблоне вариаций нет вовсе, и все три варианта оказались бы неразличимы
(ADR-0004).

**Схема определяется по цвету текста, а не по яркости фона.** В VK Tech все
14 макетов семейства «Контент» стоят на изображении, и яркость фона
статически неизвестна. Зато известен цвет, который дизайнер назначил тексту:
светлый текст означает расчёт на тёмный фон. Это заявление автора шаблона, а
не наша догадка.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

from dpd.models import ColorScheme, Layout, VariantGroup

MID_LUMINANCE = 0.5
"""Граница светлого и тёмного по относительной яркости."""


def colour_scheme(layout: Layout) -> ColorScheme:
    """Определить, светлая схема у макета или тёмная.

    Порядок сигналов: сначала цвет текста заголовка, потом — если его нет —
    яркость сплошного фона. Текст надёжнее: он есть и там, где фон картинка.
    """
    title = next((slot for slot in layout.slots if slot.kind == "title"), None)
    text_colour = title.text_style.color if title and title.text_style else None
    if text_colour:
        return "dark" if _luminance(text_colour) > MID_LUMINANCE else "light"

    if layout.background.kind == "solid" and layout.background.value:
        return "light" if _luminance(layout.background.value) > MID_LUMINANCE else "dark"

    return "unknown"


def group(layouts: list[Layout]) -> list[Layout]:
    """Связать макеты, различающиеся только цветовой схемой.

    Группируются макеты одного типа с одинаковым числом слотов: разное число
    мест под содержимое означает разную композицию, а не вариацию цвета.
    """
    buckets: dict[tuple, list[Layout]] = {}
    for layout in layouts:
        buckets.setdefault((layout.family, len(layout.slots)), []).append(layout)

    grouped: list[Layout] = []
    for (family, _), members in buckets.items():
        schemes = {member.color_scheme for member in members}
        if len(schemes) < 2:
            grouped.extend(members)
            continue

        for member in members:
            siblings = [
                other.id
                for other in members
                if other.id != member.id and other.color_scheme != member.color_scheme
            ]
            grouped.append(
                member.model_copy(
                    update={
                        "variant_group": VariantGroup(
                            family=family,
                            siblings=siblings,
                            differs_by="colorScheme",
                        )
                    }
                )
            )

    # Порядок макетов в схеме значим: по нему вёрстка выбирает первый
    # подходящий, и перестановка сделала бы результат невоспроизводимым.
    order = {layout.id: index for index, layout in enumerate(layouts)}
    return sorted(grouped, key=lambda layout: order[layout.id])


def _luminance(colour: str) -> float:
    """Относительная яркость цвета по формуле восприятия."""
    red, green, blue = (int(colour[index : index + 2], 16) for index in (1, 3, 5))
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255
