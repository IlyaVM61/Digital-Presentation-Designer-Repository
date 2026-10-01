"""Гибридная проверка контраста текста к фону (T-32).

Единственная проверка Приложения 1, не укладывающаяся в дихотомию ТЗ. Ради
неё на фазе 6 введён третий класс — `rendered`, и здесь он отрабатывает.

**Две ветви одного вопроса.** Когда фон сплошной, цвет под текстом записан в
файле, и контраст — арифметика по кодам: класс `file`, воспроизводимость
абсолютная. Когда фоном служит изображение, кода цвета не существует вовсе:
ответ дают пиксели рендера. Это по-прежнему не касается смысла и не требует
модели, но воспроизводимость становится условной — она зависит от окружения
рендера. Поэтому класс у таких находок `rendered`, и каркас проставляет им
сведения об окружении: без установленных гарнитур шаблона метрики текста
меняются, и результат может отличаться.

**Это основной случай, а не исключение.** В VK Tech фоном служит изображение
у 24 макетов из 39. Отказ от пиксельной ветви оставил бы без проверки
большинство слайдов этого шаблона.

**Крупный текст держится другого порога.** Так устроен сам стандарт, на
который мы ссылаемся: для крупного начертания достаточно 3:1. Брендовый синий
VK на белом даёт 4,13:1 — для основного текста это нарушение, для заголовка
в 32 пункта нет, и требовать от заголовка 4,5:1 значило бы противоречить
стандарту.

**Таблицы и диаграммы меряются, как текст** (T-56). До T-56 проверка их не
видела: смотрела только текстовые блоки, а заливку ячеек и цвет подписей
диаграммы вёрстка не задавала — шапку красил стиль таблицы по умолчанию
цветом акцента темы, подписи — программа просмотра. Сравнивать было не с
чем, и шапка цвета своей заливки прошла мимо. Теперь под шапкой — её
заливка, известная кодом цвета и на фоне-изображении; под строками и
подписями — фон слайда.

**Незаданный цвет текста нарушением не считается.** Его разрешает шаблон по
цепочке наследования, и судить тут не о чем — ровно как в проверках раздела
«Шаблон».
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from dpd.audit.registry import CheckSpec, check, param
from dpd.models import (
    Bounds,
    Finding,
    RenderedPresentation,
    TemplateSchema,
)

SPEC = CheckSpec(
    id="template.contrast_low",
    category="template",
    check_class="file",
    severity="warning",
    fixability="lossy",
    sublayer="4b",
    title="Контраст текста к фону ниже 4,5:1",
    plain="Текст плохо читается на фоне",
)
"""Заявленный класс — `file`: это ветвь, работающая без рендера. Пиксельная
ветвь помечает свои находки классом `rendered` сама."""


@check(SPEC)
def check_contrast_low(
    deck: RenderedPresentation,
    template: TemplateSchema,
    images: list[Path] | None = None,
    min_ratio: float | None = None,
    large_text_pt: float | None = None,
    large_text_ratio: float | None = None,
) -> list[Finding]:
    """Найти текст, который плохо читается на своём фоне.

    Без рендера пиксельная ветвь не работает, и слайды с фоном-изображением
    остаются неизмеренными. Молчание о них — не утверждение, что контраст в
    порядке: выдумывать цвет фона нельзя, а объявлять слайд нарушителем без
    измерения — тем более. Проверка выполняется повторно после рендера, и
    тогда отвечает по пикселям.
    """
    min_ratio = _threshold(min_ratio, "min_ratio")
    large_text_pt = _threshold(large_text_pt, "large_text_pt")
    large_text_ratio = _threshold(large_text_ratio, "large_text_ratio")

    layouts = {layout.id: layout for layout in template.layouts}
    renders = _renders(images, len(deck.slides))

    findings: list[Finding] = []
    for number, slide in enumerate(deck.slides, start=1):
        layout = layouts.get(slide.layout_id)
        if layout is None:
            continue

        background = layout.background
        computable = background.contrast_computable and bool(background.value)
        render = renders.get(number)

        # Рендер открывается один раз на слайд, а не на каждый прогон текста.
        picture = None if computable or render is None else _load(render)

        for element in slide.elements:
            for part, colour, smallest, runs, fill in _texts(element):
                # Под шапкой таблицы — её заливка, код цвета известен и на
                # фоне-изображении.
                if fill:
                    source, behind = "file", fill
                elif computable:
                    source, behind = "file", background.value
                elif picture is not None:
                    source, behind = "rendered", _behind(picture, element.bounds)
                else:
                    continue

                ratio = _contrast(colour, behind)
                required = large_text_ratio if smallest >= large_text_pt else min_ratio
                if ratio >= required:
                    continue

                where = f"блок «{element.slot_id}»" + (f", {part}" if part else "")
                under = "заливке" if fill else "фону"
                evidence = {
                    "ratio": round(ratio, 2),
                    "expected": required,
                    "text": colour,
                    "background": behind,
                    "runs": runs,
                    "measuredBy": "коды цветов" if source == "file" else "пиксели рендера",
                }
                if part:
                    evidence["part"] = part
                findings.append(
                    SPEC.finding(
                        f"Слайд {number}, {where}: контраст текста "
                        f"{colour} к {under} {behind} — {ratio:.2f}:1, это ниже {required:g}:1.",
                        check_class=source,
                        slide_number=number,
                        slot_id=element.slot_id,
                        evidence=evidence,
                    )
                )
    return findings


def _texts(element) -> list[tuple[str | None, str, float, int, str | None]]:
    """Текст элемента парами с тем, что под ним (T-56).

    Каждая запись — часть элемента, цвет текста, самый мелкий кегль, число
    прогонов или ячеек и заливка под текстом, если она своя. Таблицы и
    диаграммы раньше сюда не попадали: проверка смотрела только текстовые
    блоки, а цвет заливки таблицы и подписей диаграммы вёрстка не задавала —
    сравнивать было не с чем.
    """
    if element.kind == "text":
        return [(None, colour, smallest, runs, None) for colour, (smallest, runs) in _by_colour(element).items()]

    texts = []
    table, chart = element.table, element.chart
    if table is not None:
        size = table.size_pt or 0.0
        if table.header_color and any(cell.strip() for cell in table.headers):
            texts.append(("шапка таблицы", table.header_color, size, len(table.headers), table.header_fill))
        if table.body_color and any(cell.strip() for row in table.rows for cell in row):
            texts.append(("строки таблицы", table.body_color, size, len(table.rows), None))
    if chart is not None and chart.text_color:
        texts.append(("подписи диаграммы", chart.text_color, chart.size_pt or 0.0, 1, None))
    return texts


def _by_colour(element) -> dict[str, tuple[float, int]]:
    """Цвета текста элемента: для каждого — самый мелкий кегль и число прогонов.

    Одно нарушение должно давать одну находку: список из пяти пунктов
    серого текста — это один дефект цвета, а не пять, и пять одинаковых
    сообщений заставили бы искать пять разных причин.

    Кегль берётся наименьший: порог зависит от размера, и цвет, которым
    набраны и заголовок, и подпись, обязан проверяться по подписи.
    """
    colours: dict[str, tuple[float, int]] = {}
    for run in element.runs:
        if not run.color or not run.text.strip():
            continue
        size = run.size_pt or 0.0
        smallest, count = colours.get(run.color, (size, 0))
        colours[run.color] = (min(smallest, size), count + 1)
    return colours


def _threshold(value: float | None, name: str) -> float:
    return float(value) if value is not None else float(param(SPEC.id, name))


def _renders(images: list[Path] | None, slides: int) -> dict[int, Path]:
    """Сопоставить рендеры слайдам по порядку.

    Несовпадение числа рендеров с числом слайдов означает, что сопоставление
    неизвестно. Считать их совпадающими «примерно» нельзя: ошибка привела бы
    к измерению контраста не на том слайде — то есть к находке, которая
    выглядит обоснованной и при этом неверна.
    """
    if not images or len(images) != slides:
        return {}
    return {number: path for number, path in enumerate(images, start=1)}


SAMPLE_SIDE = 64
"""Сторона выборки пикселей области. Прореживание идёт ближайшим соседом, а
не усреднением: усреднение смешало бы цвет текста с цветом фона и дало бы
величину, которой на слайде нет."""


def _load(render: Path | None) -> Image.Image | None:
    if render is None:
        return None
    with Image.open(render) as image:
        return image.convert("RGB")


def _behind(picture: Image.Image | None, bounds: Bounds) -> str:
    """Оценить цвет фона под элементом по рендеру слайда.

    Берётся медианный по яркости пиксель области: текст занимает меньшую
    часть своей рамки, и медиана приходится на фон. Среднее для этого не
    годится по той же причине, по которой не годится усреднённое прореживание.
    """
    if picture is None:
        return "#FFFFFF"

    width, height = picture.size
    left = max(int(bounds.x * width), 0)
    top = max(int(bounds.y * height), 0)
    right = min(int((bounds.x + bounds.w) * width), width)
    bottom = min(int((bounds.y + bounds.h) * height), height)
    if right <= left or bottom <= top:
        return "#FFFFFF"

    area = picture.crop((left, top, right, bottom))
    if area.width * area.height > SAMPLE_SIDE * SAMPLE_SIDE:
        side = (min(area.width, SAMPLE_SIDE), min(area.height, SAMPLE_SIDE))
        area = area.resize(side, Image.Resampling.NEAREST)

    pixels = sorted(area.get_flattened_data(), key=_pixel_luminance)
    red, green, blue = pixels[len(pixels) // 2]
    return f"#{red:02X}{green:02X}{blue:02X}"


def _pixel_luminance(pixel: tuple[int, int, int]) -> float:
    return _relative_luminance(*(channel / 255 for channel in pixel))


def _contrast(first: str, second: str) -> float:
    """Отношение контраста по WCAG: (светлее + 0,05) / (темнее + 0,05)."""
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _luminance(colour: str) -> float:
    channels = (int(colour[index : index + 2], 16) / 255 for index in (1, 3, 5))
    return _relative_luminance(*channels)


def _relative_luminance(red: float, green: float, blue: float) -> float:
    linear = [
        value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
        for value in (red, green, blue)
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
