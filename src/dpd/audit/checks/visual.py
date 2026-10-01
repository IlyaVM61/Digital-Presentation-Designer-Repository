"""Валидация контента по изображению слайда: подслой 4c (T-52).

Вопрос 6 раздела «Валидация контента» Приложения 1 — картинки и пиктограммы
по теме слайда — и визуальная читаемость, добавленная сверх набора ТЗ: она
ловит то, что порогами не описать, — текст цвета заливки, подписи, слитые с
фоном, заголовок поверх текста.

**Проверки читают ответ модели, а не задают его.** Запрос — один на слайд
(`dpd.audit.visual`), ответы лежат в контексте аудита как `visual_review`.
Без них проверки пропускаются, и это видно в отчёте.

**Читаемость сверяет код.** Модель переписывает текст, который видит на
изображении, и помечает строки, разбираемые с трудом; проверка ищет в этой
расшифровке каждую строку, которую положила вёрстка. Не нашлась — зритель её
не увидит: это предупреждение, содержимое потеряно. Нашлась в строке с
пометкой «с трудом» — рекомендация. Расшифровка неточна, поэтому строка
считается увиденной, если прочитана доля её слов из `configs/audit.yaml`.

**Оформление шаблона не оценивается** (правило 12 CLAUDE.md): сверяются
только строки, положенные вёрсткой, — надписи шаблона в расшифровке не
проверяются, а фон, логотипы и декор промпт исключает из вопроса о картинках.

Каждая находка несёт `evidence.slideId`: по нему отчёт варианта находит
слайд, даже когда номера сдвинулись.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from dpd.audit.registry import CheckSpec, check
from dpd.audit.textual import plain
from dpd.audit.visual import element_texts
from dpd.models import Finding, RenderedPresentation

if TYPE_CHECKING:
    from dpd.audit.visual import VisualReview

IRRELEVANT_IMAGERY = CheckSpec(
    id="content.irrelevant_imagery",
    category="content",
    check_class="model",
    severity="warning",
    fixability="semantic",
    sublayer="4c",
    title="Картинки и иконки не относятся к теме слайда",
)
VISUAL_READABILITY = CheckSpec(
    id="content.visual_readability",
    category="content",
    check_class="model",
    severity="advice",
    fixability="lossy",
    sublayer="4c",
    title="Текст на слайде не видно или он читается с трудом",
)

WORD = re.compile(r"\w+")
MAX_QUOTED = 5


@check(VISUAL_READABILITY)
def check_visual_readability(
    deck: RenderedPresentation,
    visual_review: VisualReview,
    min_seen_share: float,
) -> list[Finding]:
    """Строки, которых на изображении нет или которые читаются с трудом, — на своём блоке."""
    findings: list[Finding] = []
    for number, (slide, look) in enumerate(zip(deck.slides, visual_review.slides, strict=True), start=1):
        seen = {word for line in look.lines for word in _words(line.text)}
        faint = [_words(line.text) for line in look.lines if line.legibility == "faint"]
        for position, element in enumerate(slide.elements, start=1):
            missing: list[str] = []
            strained: list[str] = []
            for text in dict.fromkeys(element_texts(element)):
                words = _words(text)
                if not words:
                    continue
                if _share(words, seen) < min_seen_share:
                    missing.append(text)
                elif any(len(words & line) / max(len(words), len(line)) >= min_seen_share for line in faint):
                    strained.append(text)
            evidence = {"slideId": slide.id, "element": position}
            if missing:
                findings.append(
                    VISUAL_READABILITY.finding(
                        f"На изображении слайда не видно {_quoted(missing)}: текст сливается с фоном, "
                        "закрыт другим или ушёл за край",
                        severity="warning",
                        slide_number=number,
                        slot_id=element.slot_id,
                        evidence={**evidence, "missing": missing},
                    )
                )
            if strained:
                findings.append(
                    VISUAL_READABILITY.finding(
                        f"Читается с трудом {_quoted(strained)}: буквы почти сливаются с фоном",
                        slide_number=number,
                        slot_id=element.slot_id,
                        evidence={**evidence, "faint": strained},
                    )
                )
    return findings


@check(IRRELEVANT_IMAGERY)
def check_irrelevant_imagery(deck: RenderedPresentation, visual_review: VisualReview) -> list[Finding]:
    """Вопрос 6: картинка с сюжетом, не связанным с темой слайда."""
    findings: list[Finding] = []
    for number, (slide, look) in enumerate(zip(deck.slides, visual_review.slides, strict=True), start=1):
        for picture in look.off_topic_pictures:
            shows, why = picture.shows.strip(), picture.why.strip()
            findings.append(
                IRRELEVANT_IMAGERY.finding(
                    f"Картинка не по теме слайда — {shows}: {why}",
                    slide_number=number,
                    evidence={"slideId": slide.id, "shows": shows, "why": why},
                )
            )
    return findings


def _words(text: str) -> set[str]:
    """Слова строки для сравнения: регистр, «ё», кавычки и знаки не важны."""
    return set(WORD.findall(plain(text)))


def _share(words: set[str], seen: set[str]) -> float:
    return len(words & seen) / len(words)


def _quoted(texts: list[str]) -> str:
    shown = ", ".join(f"«{text}»" for text in texts[:MAX_QUOTED])
    rest = len(texts) - MAX_QUOTED
    return f"{shown} и ещё {rest}" if rest > 0 else shown


__all__ = ["check_irrelevant_imagery", "check_visual_readability"]
