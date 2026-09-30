"""Применение дизайн-токенов шаблона к укладываемому тексту.

**Вёрстка не выбирает оформление — она применяет прочитанные правила.**
Цвет или гарнитура, которых в шаблоне нет, означали бы, что система сочинила
дизайн вместо того, чтобы его соблюсти.

Источник оформления — сам слот: он несёт стиль, разрешённый по цепочке
наследования, и это самое точное, что известно о конкретном месте макета.
Токены шаблона нужны там, где слот молчит, и как страховка: кегль,
отсутствующий в очищенной шкале, заменяется ближайшим из неё.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from dpd.models import Decision, DesignTokens, Slot, TextRun, TextStyle

WEAK_LEVELS = ("master.", "theme.", "default")
"""Уровни, с которых свойство приходит как объявление шаблона, а не как его
фактическая разметка. Тема расходится с оформлением во всех четырёх
проверенных шаблонах, а мастер её повторяет."""

CONFIG_ENV = "DPD_LAYOUT_CONFIG"
DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "configs" / "layout.yaml"
DEFAULT_RULE = "markup"


def conflict_rule() -> str:
    """Чему верить при расхождении: `markup` или `template`.

    Правило — данные, а не код: пользователь вправе решить иначе, и правка
    не должна требовать изменений в исходниках.
    """
    path = Path(os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG)
    if not path.is_file():
        return DEFAULT_RULE
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return str(raw.get("conflict_rule", DEFAULT_RULE))


def _declared(level: str | None) -> bool:
    """Пришло ли свойство с уровня объявления, а не разметки."""
    return bool(level) and level.startswith(WEAK_LEVELS)

TITLE_SCALE_POSITION = 0.75
"""Доля шкалы снизу, откуда берётся кегль заголовка, если слот его не знает."""


def style_for(
    slot: Slot,
    tokens: DesignTokens | None,
    size_shift: int = 0,
    decisions: list[Decision] | None = None,
    rule: str | None = None,
) -> TextStyle:
    """Оформление для содержимого слота: из слота, с опорой на токены.

    `size_shift` сдвигает кегль на ступени шкалы шаблона — так выражается
    визуальный регистр варианта. Сдвиг идёт внутри шкалы: вариант не
    выходит за правила шаблона, иначе аудит справедливо на него
    пожаловался бы.
    """
    base = slot.text_style
    rule = rule or conflict_rule()
    записать = decisions if decisions is not None else []

    # Свойство, доставшееся с уровня мастера или темы, слабее частотного
    # токена: это объявление шаблона, а не то, чем он набран. В VK Tech
    # Arial встречается один раз из 774 прогонов — и попадал в основной
    # текст именно так.
    объявленный_шрифт = base.font if base else None
    объявленный_кегль = base.size_pt if base else None
    слабый_шрифт = rule == "markup" and _declared(base.font_resolved_from if base else None)
    слабый_кегль = rule == "markup" and _declared(base.resolved_from if base else None)

    font = _font_within_template(None if слабый_шрифт else объявленный_шрифт, tokens)
    colour = base.color if base and base.color else _primary_colour(tokens)
    size = _snap_to_scale(None if слабый_кегль else объявленный_кегль, slot, tokens)
    size = _shift_by_steps(size, tokens, size_shift)

    if слабый_шрифт and объявленный_шрифт and font and font != объявленный_шрифт:
        записать.append(
            Decision(
                kind="font",
                slot_id=slot.id,
                chosen=font,
                declared=объявленный_шрифт,
                reason=(
                    f"шаблон объявляет {объявленный_шрифт} на уровне мастера, "
                    f"но набран {font}: система следует разметке"
                ),
            )
        )
    if слабый_кегль and объявленный_кегль and size and size != объявленный_кегль:
        записать.append(
            Decision(
                kind="size",
                slot_id=slot.id,
                chosen=f"{size:g} pt",
                declared=f"{объявленный_кегль:g} pt",
                reason=(
                    f"кегль {объявленный_кегль:g} pt унаследован от мастера; "
                    f"взята ступень шкалы шаблона {size:g} pt"
                ),
            )
        )

    return TextStyle(
        font=font,
        size_pt=size,
        color=colour,
        resolved_from=base.resolved_from if base else "designTokens",
        font_resolved_from=base.font_resolved_from if base else "designTokens",
    )


def apply(style: TextStyle, text: str) -> TextRun:
    """Собрать прогон с явным оформлением.

    Оформление задаётся явно, а не оставляется на усмотрение экспорта:
    иначе одно и то же содержимое выглядело бы по-разному в зависимости от
    того, попало оно в родной плейсхолдер или в созданную рамку.
    """
    return TextRun(text=text, font=style.font, size_pt=style.size_pt, color=style.color)


def _font_within_template(font: str | None, tokens: DesignTokens | None) -> str | None:
    """Гарнитура слота, если она есть в составе шаблона; иначе основная.

    Слот может унаследовать гарнитуру от мастера или темы, а тема врёт: во
    всех проверенных шаблонах она объявляет Arial, тогда как размечено Play.
    Сконструированный слот наследует именно оттуда — и получил бы Arial,
    которого на слайдах шаблона почти нет.

    Токены собраны частотным анализом фактической разметки и в этом споре
    правы: побеждает то, чем шаблон действительно набран.
    """
    families = [token.family for token in tokens.fonts] if tokens and tokens.fonts else []
    if font and (not families or font in families):
        return font
    return families[0] if families else font


def _primary_colour(tokens: DesignTokens | None) -> str | None:
    if not tokens or not tokens.colors:
        return None
    text_colour = next(
        (token for token in tokens.colors if token.role == "text.primary"), tokens.colors[0]
    )
    return text_colour.value


def _snap_to_scale(size: float | None, slot: Slot, tokens: DesignTokens | None) -> float | None:
    """Привести кегль к очищенной шкале шаблона.

    Кегль слота может оказаться среди исключённых: слот наследует свойства
    у разметки, а в ней есть следы автоподгонки. Ставить такой кегль нельзя —
    ради этого шкала и чистилась (T-16). Берётся ближайший из шкалы, а при
    полном её отсутствии значение остаётся как есть.
    """
    scale = tokens.type_scale.values if tokens else []
    if not scale:
        return size

    if size is not None and size in scale:
        return size
    if size is not None:
        return min(scale, key=lambda value: abs(value - size))

    # Слот не знает кегля: заголовку — крупный конец шкалы, тексту — средний.
    position = TITLE_SCALE_POSITION if slot.kind == "title" else 0.4
    return scale[min(int(len(scale) * position), len(scale) - 1)]


def _shift_by_steps(size: float | None, tokens: DesignTokens | None, steps: int) -> float | None:
    """Сдвинуть кегль на ступени вверх или вниз по шкале шаблона.

    Ступень, а не проценты: шкала шаблона дискретна, и промежуточные
    значения в ней не предусмотрены дизайнером. На краю шкалы сдвиг
    упирается в границу — заголовок не станет мельче основного текста.
    """
    scale = tokens.type_scale.values if tokens else []
    if size is None or not steps or size not in scale:
        return size
    index = scale.index(size)
    return scale[min(max(index + steps, 0), len(scale) - 1)]
