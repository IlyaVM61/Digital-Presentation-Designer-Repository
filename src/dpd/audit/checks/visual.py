"""Валидация контента по изображению слайда: подслой 4c (T-52).

Вопрос 6 раздела «Валидация контента» Приложения 1 — картинки и пиктограммы
по теме слайда — и визуальная читаемость, добавленная сверх набора ТЗ: она
ловит то, что порогами не описать, — текст цвета заливки, подписи, слитые с
фоном, заголовок поверх текста.

**Проверки читают ответ модели, а не задают его.** Запрос — один на слайд
(`dpd.audit.visual`), ответы лежат в контексте аудита как `visual_review`.
Без них проверки пропускаются, и это видно в отчёте.

**Оформление шаблона не оценивается** (правило 12 CLAUDE.md): замечание о
читаемости обязано указать элемент, который положила вёрстка, а картинки
шаблона отмечаются, только если их сюжет не связан с темой слайда, — фон,
логотипы и декор промпт исключает.

Каждая находка несёт `evidence.slideId`: по нему отчёт варианта находит
слайд, даже когда номера сдвинулись.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dpd.audit.registry import CheckSpec, check
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
    title="Слайд трудно прочитать: текст сливается с фоном или элементы выглядят случайными",
)


@check(VISUAL_READABILITY)
def check_visual_readability(deck: RenderedPresentation, visual_review: VisualReview) -> list[Finding]:
    """Текст и данные, которые на изображении не прочитать, — на своём блоке."""
    findings: list[Finding] = []
    for number, (slide, look) in enumerate(zip(deck.slides, visual_review.slides, strict=True), start=1):
        for issue in look.readability:
            problem = issue.problem.strip()
            findings.append(
                VISUAL_READABILITY.finding(
                    f"Слайд трудно прочитать: {problem}",
                    slide_number=number,
                    slot_id=slide.elements[issue.element - 1].slot_id,
                    evidence={"slideId": slide.id, "element": issue.element, "problem": problem},
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


__all__ = ["check_irrelevant_imagery", "check_visual_readability"]
