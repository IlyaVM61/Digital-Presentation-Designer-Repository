"""Определение вида фона макета.

**Контраст статически вычислим не всегда.** В 24 макетах VK Tech из 39 фоном
служит изображение, и цвет под текстом неизвестен до рендера — коды цветов о
нём молчат. Отсюда решение фазы 6: проверка 4.5:1 гибридная, класс `file`
там, где фон сплошной, и класс `rendered` там, где он картинка.

Фон ищется в двух местах. Сначала в элементе `p:bg` макета — так его задаёт
PowerPoint. Затем среди фигур: картинка, покрывающая холст целиком, работает
фоном независимо от того, как она объявлена, и для проверки контраста
неотличима от настоящего фона.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

from dpd.models import Background

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"

FULL_CANVAS_SHARE = 0.9
"""Доля холста, при которой картинка работает фоном.

Измерено: фоновые изображения покрывают холст целиком, крупные иллюстрации
занимают заметно меньше, промежуточных значений в калибровочных шаблонах
нет. Порог попадает в разрыв, а не делит непрерывный ряд."""


def detect(layout, canvas, scheme: dict[str, str] | None = None) -> Background:
    """Определить фон макета и вычислимость контраста на нём."""
    declared = _from_background_element(layout, scheme)
    if declared is not None and declared.kind == "image":
        return declared

    covering = _covering_picture(layout, canvas)
    if covering:
        return Background(
            kind="image",
            source="layout.shape.picture",
            contrast_computable=False,
        )

    return declared or Background(
        kind="inherited",
        source="master.bg",
        contrast_computable=True,
    )


def _from_background_element(layout, scheme: dict[str, str] | None) -> Background | None:
    """Фон из `p:bg` макета — то, как его задаёт PowerPoint."""
    element = layout.element.find(P + "cSld/" + P + "bg")
    if element is None:
        return None

    if element.find(".//" + A + "blipFill") is not None:
        return Background(kind="image", source="layout.bg.blipFill", contrast_computable=False)

    if element.find(".//" + A + "gradFill") is not None:
        # Градиент вычислим в принципе, но цвет под текстом зависит от точки,
        # поэтому считаем его нерешаемым статически так же, как изображение.
        return Background(
            kind="gradient", source="layout.bg.gradFill", contrast_computable=False
        )

    solid = element.find(".//" + A + "solidFill")
    if solid is not None:
        value, source = _solid_colour(solid, scheme)
        return Background(
            kind="solid", value=value, source=source, contrast_computable=value is not None
        )
    return None


def _solid_colour(fill, scheme: dict[str, str] | None) -> tuple[str | None, str]:
    direct = fill.find(A + "srgbClr")
    if direct is not None and direct.get("val"):
        return "#" + direct.get("val").upper(), "layout.bg.srgbClr"

    referenced = fill.find(A + "schemeClr")
    name = referenced.get("val") if referenced is not None else None
    if name:
        name = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}.get(name, name)
        resolved = (scheme or {}).get(name)
        if resolved:
            return "#" + resolved.upper(), f"layout.bg.schemeClr:{name}"
        return None, f"layout.bg.schemeClr:{name}"
    return None, "layout.bg.solidFill"


def _covering_picture(layout, canvas) -> bool:
    """Есть ли картинка, накрывающая холст.

    Такая картинка фактически и есть фон: для текста поверх неё контраст по
    кодам цветов не посчитать, как бы она ни была объявлена в разметке.
    """
    total = canvas.width_emu * canvas.height_emu
    for shape in layout.shapes:
        if not shape.shape_type or "PICTURE" not in str(shape.shape_type):
            continue
        if not shape.width or not shape.height:
            continue
        if shape.width * shape.height / total >= FULL_CANVAS_SHARE:
            return True
    return False
