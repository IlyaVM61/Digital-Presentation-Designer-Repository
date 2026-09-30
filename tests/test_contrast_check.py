"""T-32: гибридная проверка контраста.

Критерий приёмки: **на сплошном фоне контраст считается по кодам цветов, на
фоне-изображении — по пикселям**. Это единственная проверка Приложения 1, не
укладывающаяся в дихотомию ТЗ, и ради неё на фазе 6 введён класс `rendered`.

Обе ветви обязаны заявлять о себе честно: находка по кодам несёт класс
`file`, находка по пикселям — `rendered` и сведения об окружении рендера.
Выдавать вторую за первую нельзя: её воспроизводимость зависит от того,
установлены ли гарнитуры шаблона, и отчёт обязан это оговаривать.

Почему это не редкий случай: в VK Tech фоном служит изображение у 24 макетов
из 39. Пиксельная ветвь — основной режим для этого шаблона, а не запасной.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from dpd.audit import REGISTRY, AuditContext, discover, run_checks
from dpd.layout import compose_variants
from dpd.models import (
    Background,
    Bounds,
    Canvas,
    Layout,
    PresentationStructure,
    RenderedElement,
    RenderedPresentation,
    Slide,
    SlideBody,
    Slot,
    StructureSlide,
    TemplateSchema,
    TemplateSource,
    TextRun,
)
from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"
CANVAS = Canvas(width_emu=12192000, height_emu=6858000)
BODY_BOUNDS = Bounds(x=0.1, y=0.3, w=0.6, h=0.3)

CHECK_ID = "template.contrast_low"


def schema(*, kind: str = "solid", value: str | None = "#FFFFFF", computable: bool = True) -> TemplateSchema:
    return TemplateSchema(
        source=TemplateSource(file="synthetic.pptx", hash="0" * 8),
        canvas=CANVAS,
        layouts=[
            Layout(
                id="l1",
                name="Макет",
                family="content",
                background=Background(kind=kind, value=value, contrast_computable=computable),
                slots=[Slot(id="body", kind="body", origin="placeholder", bounds=BODY_BOUNDS)],
            )
        ],
    )


def deck(colour: str = "#000000", size: float = 14.0) -> RenderedPresentation:
    return RenderedPresentation(
        variant="A",
        template_hash="0" * 8,
        canvas=CANVAS,
        slides=[
            Slide(
                id="s1",
                layout_id="l1",
                elements=[
                    RenderedElement(
                        slot_id="body",
                        kind="text",
                        bounds=BODY_BOUNDS,
                        runs=[TextRun(text="Итоги пилота", font="Play", size_pt=size, color=colour)],
                    )
                ],
            )
        ],
    )


def findings(context: AuditContext) -> list:
    discover()
    report = run_checks(context)
    assert CHECK_ID in report.checks_run, f"проверка не выполнилась: {report.checks_skipped}"
    return [item for item in report.findings if item.check_id == CHECK_ID]


@pytest.fixture
def light_render(tmp_path: Path) -> Path:
    """Рендер слайда со светлым фоном-изображением."""
    path = tmp_path / "slide-001.png"
    Image.new("RGB", (1280, 720), (238, 240, 245)).save(path)
    return path


# --- Реестр --------------------------------------------------------------


def test_check_is_registered_as_the_checklist_describes() -> None:
    """Классификация из чек-листа. Заявленный класс — `file`.

    Пиксельная ветвь помечает свои находки классом `rendered` сама: класс
    зависит не от проверки, а от того, как получен ответ на конкретном
    слайде.
    """
    discover()
    registered = REGISTRY.get(CHECK_ID)
    assert registered is not None

    spec = registered.spec
    assert (spec.category, spec.check_class, spec.severity, spec.fixability) == (
        "template",
        "file",
        "warning",
        "lossy",
    )


# --- Ветвь по кодам цветов ------------------------------------------------


def test_solid_background_is_measured_by_colour_codes() -> None:
    """Серый текст на белом фоне: 2,8:1 — считается арифметикой по кодам."""
    found = findings(AuditContext(deck=deck("#999999"), template=schema()))

    assert len(found) == 1
    assert found[0].check_class == "file", "находка по кодам не должна выдаваться за пиксельную"
    assert found[0].evidence["ratio"] == pytest.approx(2.8, abs=0.1)
    assert found[0].evidence["background"] == "#FFFFFF"
    assert found[0].environment is None


def test_solid_background_is_silent_on_sufficient_contrast() -> None:
    assert findings(AuditContext(deck=deck("#000000"), template=schema())) == []


def test_large_text_is_held_to_the_lower_ratio() -> None:
    """Крупный текст проходит по 3:1 — так устроен сам стандарт.

    Брендовый синий VK на белом даёт 4,13:1. Для основного текста это
    нарушение, для заголовка в 32 пункта — нет, и обвинять в нём значило бы
    противоречить тому стандарту, на который мы ссылаемся.
    """
    assert findings(AuditContext(deck=deck("#0077FF", size=32), template=schema())) == []

    small = findings(AuditContext(deck=deck("#0077FF", size=14), template=schema()))
    assert len(small) == 1
    assert small[0].evidence["ratio"] == pytest.approx(4.13, abs=0.05)


def test_one_finding_per_colour_not_per_paragraph() -> None:
    """Одно нарушение — одна находка, сколько бы абзацев им ни набрали.

    Иначе список из пяти пунктов серого текста даёт пять одинаковых
    сообщений, и пользователь ищет пять разных дефектов там, где он один.
    Число затронутых прогонов остаётся в доказательстве.
    """
    crowded = deck("#999999")
    crowded.slides[0].elements[0].runs = [
        TextRun(text=f"пункт {index}", font="Play", size_pt=14, color="#999999") for index in range(5)
    ]

    found = findings(AuditContext(deck=crowded, template=schema()))

    assert len(found) == 1
    assert found[0].evidence["runs"] == 5


def test_smallest_text_of_a_colour_sets_the_requirement() -> None:
    """Порог берётся по худшему случаю — самому мелкому тексту этого цвета.

    Иначе цвет, которым набраны и заголовок, и подпись, проходил бы по
    заголовочному порогу, и мелкая подпись осталась бы непроверенной.
    """
    mixed = deck("#0077FF")
    mixed.slides[0].elements[0].runs = [
        TextRun(text="Заголовок", font="Play", size_pt=32, color="#0077FF"),
        TextRun(text="подпись", font="Play", size_pt=12, color="#0077FF"),
    ]

    found = findings(AuditContext(deck=mixed, template=schema()))

    assert len(found) == 1
    assert found[0].evidence["expected"] == 4.5


def test_inherited_text_colour_is_not_a_violation() -> None:
    """Цвет, не заданный явно, разрешает шаблон — судить не о чем."""
    transparent = deck()
    transparent.slides[0].elements[0].runs[0].color = None

    assert findings(AuditContext(deck=transparent, template=schema())) == []


# --- Ветвь по пикселям ----------------------------------------------------


def test_image_background_is_measured_by_pixels(light_render: Path) -> None:
    """Фон-изображение: кода цвета не существует, ответ дают пиксели.

    В VK Tech так устроены 24 макета из 39 — это основной режим шаблона, а
    не исключение.
    """
    found = findings(
        AuditContext(
            deck=deck("#DDDDDD"),
            template=schema(kind="image", value=None, computable=False),
            images=[light_render],
            environment={"fontsAvailable": True},
        )
    )

    assert len(found) == 1
    assert found[0].check_class == "rendered", "пиксельная находка обязана называть свой класс"
    assert found[0].environment == {"fontsAvailable": True}, "оговорка об окружении обязательна"
    assert found[0].evidence["background"].startswith("#")


def test_image_background_is_silent_on_sufficient_contrast(light_render: Path) -> None:
    found = findings(
        AuditContext(
            deck=deck("#000000"),
            template=schema(kind="image", value=None, computable=False),
            images=[light_render],
        )
    )

    assert found == []


def test_image_background_without_a_render_is_left_unmeasured() -> None:
    """Без рендера пиксельной ветви работать нечем.

    Молчание здесь — не утверждение о том, что контраст в порядке: проверка
    выполняется повторно после рендера. Выдумывать цвет фона-изображения
    нельзя, а объявлять слайд нарушителем без измерения — тем более.
    """
    found = findings(
        AuditContext(deck=deck("#DDDDDD"), template=schema(kind="image", value=None, computable=False))
    )

    assert found == []


def test_pixel_branch_reads_the_area_under_the_element(tmp_path: Path) -> None:
    """Фон берётся из-под самого текста, а не со всего слайда.

    Слайд с тёмной половиной и светлой половиной: текст стоит на светлой, и
    ответ должен зависеть от неё, иначе на половинчатых фонах проверка
    отвечала бы наугад.
    """
    image = Image.new("RGB", (1280, 720), (10, 10, 10))
    for x in range(int(1280 * 0.05), 1280):
        for y in range(int(720 * 0.25), int(720 * 0.65)):
            image.putpixel((x, y), (245, 245, 245))
    path = tmp_path / "slide-001.png"
    image.save(path)

    dark_text = findings(
        AuditContext(
            deck=deck("#000000"),
            template=schema(kind="image", value=None, computable=False),
            images=[path],
        )
    )
    light_text = findings(
        AuditContext(
            deck=deck("#EEEEEE"),
            template=schema(kind="image", value=None, computable=False),
            images=[path],
        )
    )

    assert dark_text == [], "тёмный текст на светлой области — контраст в норме"
    assert len(light_text) == 1, "светлый текст на светлой области обязан сработать"


# --- Живые шаблоны --------------------------------------------------------


def test_solid_background_templates_are_measured_without_a_render() -> None:
    """На шаблоне со сплошными фонами ветвь по кодам работает сама по себе.

    VK Education: 27 макетов из 30 со сплошным фоном. Находка здесь
    настоящая — брендовый синий на белом даёт 4,13:1, и основной текст этого
    сочетания не проходит.
    """
    template_file = CALIBRATION / "Шаблон презентации VK Education.pptx"
    if not template_file.exists():
        pytest.skip("нет калибровочного шаблона")

    template = parse_template(template_file)
    structure = PresentationStructure(
        slides=[
            StructureSlide(id="s1", role="title", headline="Итоги пилота"),
            StructureSlide(
                id="s2",
                role="data",
                headline="Что изменилось за квартал",
                body=SlideBody(items=["Срок сборки колоды сократился с девяти дней до трёх"]),
            ),
        ]
    )

    discover()
    for variant in compose_variants(structure, template):
        report = run_checks(AuditContext(deck=variant, template=template))
        contrast = [item for item in report.findings if item.check_id == CHECK_ID]
        assert all(item.check_class == "file" for item in contrast), (
            "без рендера пиксельной ветви взяться неоткуда"
        )
