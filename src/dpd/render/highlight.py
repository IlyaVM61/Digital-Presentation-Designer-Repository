"""Подсветка находок аудита на изображении слайда (T-35).

**Аудит не блокирует экспорт ни при какой критичности** — решение принимает
пользователь, так требует ТЗ. Но увидеть критическую находку он обязан, и
подсветка делает её незаметной невозможно: рамка вокруг области нарушения и
подпись рядом.

Цвета заданы архитектурой аудита: красный — критично, жёлтый —
предупреждение, синий — рекомендация. По цвету вес находки виден без чтения
отчёта.

**Находки одной области объединяются в одну рамку.** Области совпадают чаще,
чем кажется: один блок собирает и критическую находку о выходе за холст, и
предупреждение о полях, и кегль вне шкалы. Три рамки на одном месте
вырождаются в одну — верхнюю, — зато три подписи ложатся друг на друга и
становятся нечитаемыми. Это видно только на рендере, тесты на такое молчат.

Цвет рамки берётся по самой тяжёлой находке группы: иначе критическую находку
закроет рекомендация, и пользователь увидит синюю рамку там, где слайд
непригоден. Подпись называет её же и сообщает, сколько находок скрыто за ней.

**Находка без области рамкой не обводится.** Указать на невиновный блок хуже,
чем не указать вовсе. Находка о слайде целиком получает значок в углу, а
находка о колоде — «два слайда дублируют друг друга» — не наносится на слайд
вообще и возвращается вызывающему: её место на уровне колоды.

**Подпись набирается латиницей — идентификатором находки и проверки.** Шрифт,
встроенный в библиотеку, не содержит кириллицы, а брать системный значило бы
поставить вид картинки в зависимость от машины. Русское сообщение остаётся в
отчёте; подпись связывает рамку с его строкой.

Исходный рендер не трогается: по нему считается контраст на фоне-изображении,
и рамка поверх фона испортила бы измерение.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from dpd.models import (
    AuditReport,
    Bounds,
    Finding,
    RenderedPresentation,
    Severity,
    Slide,
)

SEVERITY_COLORS: dict[Severity, tuple[int, int, int]] = {
    "critical": (214, 45, 32),
    "warning": (242, 183, 5),
    "advice": (37, 99, 235),
}
"""Цвета подсветки из `docs/04-architecture/audit-architecture.md`."""

DRAW_ORDER: tuple[Severity, ...] = ("advice", "warning", "critical")
"""Порядок отрисовки: тяжёлое рисуется последним и оказывается сверху."""

MIN_LINE = 2
LINE_SHARE = 0.004
"""Толщина рамки в долях ширины изображения: рендеры бывают разного размера."""

LABEL_SIZE = 14
BADGE_MARGIN = 8


@dataclass(frozen=True)
class Highlighted:
    """Результат подсветки: изображения и находки уровня колоды."""

    images: list[Path] = field(default_factory=list)
    deck_level: list[Finding] = field(default_factory=list)


def highlight_slides(
    images: list[Path],
    deck: RenderedPresentation,
    report: AuditReport,
    out_dir: str | Path,
    *,
    suffix: str = "-audit",
) -> Highlighted:
    """Нанести находки на рендеры слайдов.

    Число рендеров обязано совпадать с числом слайдов: сопоставление
    «примерно» привело бы к подсветке находки не на том слайде, то есть к
    указанию на невиновный блок.
    """
    if len(images) != len(deck.slides):
        raise ValueError(
            f"рендеров {len(images)}, а слайдов {len(deck.slides)}: "
            "сопоставить их не по чему"
        )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    painted: list[Path] = []
    for number, (source, slide) in enumerate(zip(images, deck.slides, strict=True), start=1):
        findings = [item for item in report.findings if item.slide_number == number]
        target = out_dir / f"{Path(source).stem}{suffix}.png"
        _paint(Path(source), slide, findings, target)
        painted.append(target)

    return Highlighted(
        images=painted,
        deck_level=[item for item in report.findings if item.slide_number is None],
    )


def _paint(source: Path, slide: Slide, findings: list[Finding], target: Path) -> None:
    """Нарисовать находки одного слайда и сохранить рядом с исходным."""
    with Image.open(source) as opened:
        image = opened.convert("RGB")

    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=LABEL_SIZE)
    width = max(int(image.width * LINE_SHARE), MIN_LINE)
    areas = {element.slot_id: element.bounds for element in slide.elements}

    grouped: dict[str, list[Finding]] = {}
    homeless: list[Finding] = []
    for finding in findings:
        bounds = areas.get(finding.slot_id) if finding.slot_id else None
        if bounds is None:
            homeless.append(finding)
        else:
            grouped.setdefault(finding.slot_id, []).append(finding)

    for slot_id, group in grouped.items():
        leading = heaviest(group)
        _frame(draw, image, font, SEVERITY_COLORS[leading.severity], area_label(group), areas[slot_id], width)

    for index, finding in enumerate(sorted(homeless, key=lambda item: DRAW_ORDER.index(item.severity), reverse=True)):
        _badge(draw, image, font, SEVERITY_COLORS[finding.severity], area_label([finding]), index)

    image.save(target)


def heaviest(findings: list[Finding]) -> Finding:
    """Самая тяжёлая находка группы: по ней берётся цвет рамки."""
    return max(findings, key=lambda item: DRAW_ORDER.index(item.severity))


def area_label(findings: list[Finding]) -> str:
    """Подпись рамки: ведущая находка и число скрытых за ней.

    Латиница намеренно: встроенный шрифт не знает кириллицы, а системный
    поставил бы вид картинки в зависимость от машины. Номер связывает рамку
    со строкой отчёта, где сообщение написано по-русски.
    """
    leading = heaviest(findings)
    number = f"{leading.id} " if leading.id else ""
    rest = f" +{len(findings) - 1}" if len(findings) > 1 else ""
    return f"{number}{leading.check_id}{rest}"


def _frame(draw, image, font, colour, label: str, bounds: Bounds, width: int) -> None:
    """Обвести область находки и подписать рамку."""
    left = _clamp(bounds.x * image.width, image.width - 1)
    top = _clamp(bounds.y * image.height, image.height - 1)
    right = _clamp((bounds.x + bounds.w) * image.width, image.width - 1)
    bottom = _clamp((bounds.y + bounds.h) * image.height, image.height - 1)

    draw.rectangle([left, top, right, bottom], outline=colour, width=width)
    _caption(draw, font, colour, label, left, top)


def _caption(draw, font, colour, label: str, left: float, top: float) -> None:
    """Подпись на плашке цвета находки.

    Плашка нужна, чтобы подпись читалась на любом фоне: слайды бывают и
    тёмными, и с фотографией во весь холст.
    """
    box = draw.textbbox((0, 0), label, font=font)
    height = box[3] - box[1] + 6
    y = max(top - height, 0)
    draw.rectangle([left, y, left + (box[2] - box[0]) + 8, y + height], fill=colour)
    draw.text((left + 4, y + 3), label, font=font, fill=(255, 255, 255))


def _badge(draw, image, font, colour, label: str, index: int) -> None:
    """Значок находки, у которой нет области на слайде."""
    box = draw.textbbox((0, 0), label, font=font)
    height = box[3] - box[1] + 6
    width = box[2] - box[0] + 8
    x = image.width - width - BADGE_MARGIN
    y = BADGE_MARGIN + index * (height + 4)

    draw.rectangle([x, y, x + width, y + height], fill=colour)
    draw.text((x + 4, y + 3), label, font=font, fill=(255, 255, 255))


def _clamp(value: float, limit: int) -> float:
    return min(max(value, 0.0), float(limit))
