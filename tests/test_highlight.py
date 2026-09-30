"""T-35: подсветка находок на изображении слайда.

Критерий приёмки: **красная, жёлтая и синяя рамки соответствуют критичности**.
Цвета заданы архитектурой аудита и проверяются по пикселям готового
изображения, а не по константе в коде: константу можно переименовать, и тест
согласился бы с любой.

Подсветка — способ сделать находку незаметной невозможно. Аудит не блокирует
экспорт ни при какой критичности: решение принимает пользователь, но увидеть
критическую находку он обязан.

Находка, не привязанная к области, на слайд не наносится — её место на уровне
колоды. Рисовать рамку «где-нибудь» значило бы указать на невиновный блок.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from dpd.models import (
    AuditReport,
    Bounds,
    Canvas,
    Finding,
    RenderedElement,
    RenderedPresentation,
    Slide,
    TextRun,
)
from dpd.render import SEVERITY_COLORS, area_label, highlight_slides

CANVAS = Canvas(width_emu=12192000, height_emu=6858000)
BODY_BOUNDS = Bounds(x=0.20, y=0.30, w=0.40, h=0.30)
WIDTH, HEIGHT = 800, 450


@pytest.fixture
def render(tmp_path: Path) -> Path:
    """Чистый белый рендер слайда."""
    path = tmp_path / "slide-001.png"
    Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255)).save(path)
    return path


def deck(slides: int = 1) -> RenderedPresentation:
    return RenderedPresentation(
        variant="A",
        template_hash="0" * 8,
        canvas=CANVAS,
        slides=[
            Slide(
                id=f"s{number}",
                layout_id="l1",
                elements=[
                    RenderedElement(
                        slot_id="body",
                        kind="text",
                        bounds=BODY_BOUNDS,
                        runs=[TextRun(text="Итоги пилота", font="Play", size_pt=14, color="#000000")],
                    )
                ],
            )
            for number in range(1, slides + 1)
        ],
    )


def finding(
    severity: str = "critical",
    *,
    check_id: str = "layout.out_of_bounds",
    slide_number: int | None = 1,
    slot_id: str | None = "body",
    finding_id: str = "f1",
) -> Finding:
    return Finding(
        check_id=check_id,
        category="layout",
        check_class="file",
        severity=severity,
        fixability="mechanical",
        message="проверочная находка",
        slide_number=slide_number,
        slot_id=slot_id,
        id=finding_id,
    )


def report(*findings: Finding) -> AuditReport:
    return AuditReport(run_id="t35", findings=list(findings))


def frame_pixel(image: Image.Image) -> tuple[int, int, int]:
    """Цвет пикселя на верхней границе области элемента."""
    left = int(BODY_BOUNDS.x * WIDTH)
    top = int(BODY_BOUNDS.y * HEIGHT)
    return image.getpixel((left + 5, top + 1))[:3]


# --- Критерий приёмки -----------------------------------------------------


@pytest.mark.parametrize("severity", ["critical", "warning", "advice"])
def test_frame_colour_matches_the_severity(render: Path, tmp_path: Path, severity: str) -> None:
    """Красная, жёлтая, синяя — по цвету рамки видно вес находки без чтения."""
    result = highlight_slides([render], deck(), report(finding(severity)), tmp_path / "out")

    with Image.open(result.images[0]) as image:
        assert frame_pixel(image.convert("RGB")) == SEVERITY_COLORS[severity]


def test_three_severities_look_different(render: Path, tmp_path: Path) -> None:
    """Три цвета различимы между собой — иначе подсветка ничего не сообщает."""
    assert len({SEVERITY_COLORS[key] for key in ("critical", "warning", "advice")}) == 3


# --- Где рисуется рамка ----------------------------------------------------


def test_frame_surrounds_the_area_of_the_finding(render: Path, tmp_path: Path) -> None:
    """Рамка обводит блок, к которому относится находка, и не закрашивает его."""
    result = highlight_slides([render], deck(), report(finding("critical")), tmp_path / "out")

    with Image.open(result.images[0]) as opened:
        image = opened.convert("RGB")
        centre = (
            int((BODY_BOUNDS.x + BODY_BOUNDS.w / 2) * WIDTH),
            int((BODY_BOUNDS.y + BODY_BOUNDS.h / 2) * HEIGHT),
        )
        outside = (int(0.9 * WIDTH), int(0.9 * HEIGHT))

        assert image.getpixel(centre)[:3] == (255, 255, 255), "содержимое слайда закрашено"
        assert image.getpixel(outside)[:3] == (255, 255, 255), "нарисовано там, где находки нет"


def test_critical_frame_is_drawn_over_the_lighter_one(render: Path, tmp_path: Path) -> None:
    """При совпадении областей сверху оказывается самая тяжёлая находка.

    Иначе критическую находку закрывает рекомендация, и пользователь видит
    синюю рамку там, где слайд непригоден.
    """
    result = highlight_slides(
        [render],
        deck(),
        report(finding("advice", finding_id="f1"), finding("critical", finding_id="f2")),
        tmp_path / "out",
    )

    with Image.open(result.images[0]) as image:
        assert frame_pixel(image.convert("RGB")) == SEVERITY_COLORS["critical"]


def test_findings_of_one_area_share_a_single_frame_and_caption() -> None:
    """Три находки на одном блоке дают одну рамку и одну подпись.

    Дефект найден рендером, а не тестом: три подписи ложились друг на друга
    и не читались вовсе. Цвет берётся по самой тяжёлой находке, а число
    скрытых за ней называется в подписи.
    """
    group = [
        finding("warning", check_id="template.size_not_in_scale", finding_id="f3"),
        finding("critical", check_id="layout.out_of_bounds", finding_id="f1"),
        finding("warning", check_id="layout.margin_violation", finding_id="f2"),
    ]

    assert area_label(group) == "f1 layout.out_of_bounds +2"
    assert area_label([group[0]]) == "f3 template.size_not_in_scale"


def test_finding_without_an_area_is_marked_but_not_framed(render: Path, tmp_path: Path) -> None:
    """Находка о слайде целиком получает значок, а не рамку вокруг чужого блока.

    Указать рамкой на невиновный блок хуже, чем не указать вовсе.
    """
    result = highlight_slides(
        [render],
        deck(),
        report(finding("warning", check_id="density.too_many_bullets", slot_id=None)),
        tmp_path / "out",
    )

    with Image.open(result.images[0]) as opened:
        image = opened.convert("RGB")
        assert frame_pixel(image) == (255, 255, 255), "нарисована рамка вокруг блока"
        assert SEVERITY_COLORS["warning"] in set(image.get_flattened_data()), "значок не нанесён"


def test_deck_level_finding_is_returned_not_drawn(render: Path, tmp_path: Path) -> None:
    """Находка без слайда — свойство колоды, и на слайде ей места нет.

    «Два слайда дублируют друг друга» не принадлежит ни одному из них.
    """
    duplicate = finding("warning", check_id="integrity.duplicate_slides", slide_number=None, slot_id=None)

    result = highlight_slides([render], deck(), report(duplicate), tmp_path / "out")

    assert [item.check_id for item in result.deck_level] == ["integrity.duplicate_slides"]
    with Image.open(result.images[0]) as painted, Image.open(render) as source:
        assert painted.convert("RGB").get_flattened_data() == source.convert("RGB").get_flattened_data()


# --- Работа с файлами ------------------------------------------------------


def test_source_render_is_left_untouched(render: Path, tmp_path: Path) -> None:
    """Подсветка пишет рядом: исходный рендер нужен и для других целей.

    По нему считается контраст на фоне-изображении, и рамка поверх фона
    испортила бы измерение.
    """
    before = render.read_bytes()

    highlight_slides([render], deck(), report(finding()), tmp_path / "out")

    assert render.read_bytes() == before


def test_every_slide_gets_an_image_even_without_findings(tmp_path: Path) -> None:
    """Число изображений равно числу слайдов: интерфейсу нужен полный ряд."""
    renders = []
    for number in (1, 2):
        path = tmp_path / f"slide-{number:03}.png"
        Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255)).save(path)
        renders.append(path)

    result = highlight_slides(renders, deck(slides=2), report(finding(slide_number=2)), tmp_path / "out")

    assert len(result.images) == 2
    assert all(path.is_file() for path in result.images)


def test_mismatched_render_count_is_refused(render: Path, tmp_path: Path) -> None:
    """Несовпадение числа рендеров и слайдов — ошибка прогона, а не повод гадать.

    Сопоставление «примерно» привело бы к подсветке находки не на том
    слайде, то есть к указанию на невиновный блок.
    """
    with pytest.raises(ValueError, match="слайд"):
        highlight_slides([render], deck(slides=2), report(finding()), tmp_path / "out")
