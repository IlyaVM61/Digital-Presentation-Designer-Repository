"""Контракты между слоями: pydantic-модели, общие для всего пайплайна.

`TemplateSchema`, `PresentationStructure`, `RenderedPresentation`,
`AuditReport`. Здесь же генерируются JSON Schema для валидации ответов
моделей — один инструмент вместо двух.

Координаты в `TemplateSchema` — нормализованные доли, а не EMU: холсты
шаблонов различаются (9144000 против 12192000 EMU при одном 16:9), и пересчёт
в EMU выполняется только при экспорте.

Реализованы контракты вертикального среза: `TemplateSchema`,
`RenderedPresentation` и `PresentationStructure`. `AuditReport` появится
задачей своего слоя.
"""

from dpd.models.audit import Finding, Fixability, Severity
from dpd.models.common import Bounds, Canvas, Contract, TextRun
from dpd.models.rendered import (
    Compensation,
    ElementKind,
    LayoutDecision,
    RenderedElement,
    RenderedPresentation,
    Slide,
)
from dpd.models.structure import (
    BodyKind,
    PresentationStructure,
    SlideBody,
    StructureMeta,
    StructureSlide,
)
from dpd.models.template import (
    Background,
    BackgroundKind,
    ColorScheme,
    Layout,
    LayoutFamily,
    MarkupQuality,
    ParsingStrategy,
    Slot,
    SlotKind,
    SlotOrigin,
    TemplateSchema,
    TemplateSource,
    TextStyle,
    VariantGroup,
)
from dpd.models.tokens import ColorToken, DesignTokens, FontToken, Token, TypeScale

__all__ = [
    "Background",
    "BackgroundKind",
    "BodyKind",
    "Bounds",
    "Canvas",
    "ColorScheme",
    "ColorToken",
    "Compensation",
    "Contract",
    "DesignTokens",
    "ElementKind",
    "Finding",
    "Fixability",
    "FontToken",
    "Layout",
    "LayoutDecision",
    "LayoutFamily",
    "MarkupQuality",
    "ParsingStrategy",
    "PresentationStructure",
    "RenderedElement",
    "RenderedPresentation",
    "Severity",
    "Slide",
    "SlideBody",
    "Slot",
    "SlotKind",
    "SlotOrigin",
    "StructureMeta",
    "StructureSlide",
    "TemplateSchema",
    "TemplateSource",
    "TextRun",
    "TextStyle",
    "Token",
    "TypeScale",
    "VariantGroup",
]
