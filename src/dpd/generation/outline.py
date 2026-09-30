"""План колоды, введённый человеком, — в контракт `PresentationStructure`.

**Это не генерация.** Смысл приносит пользователь: он пишет заголовки и
пункты, а функция лишь переводит написанное в контракт. Настоящая генерация
структуры по свободному брифу — задача T-49, и она работает моделью, как
предписывает ключевой принцип архитектуры: модель отвечает за смысл,
детерминированный движок — за форму.

Путь нужен раньше неё по двум причинам. Интерфейсу (T-38) нужно что-то
работающее от файла до экспорта уже сейчас. И он останется полезным потом: на
защите даёт воспроизводимую колоду без обращения к модели, а пользователю —
способ поправить план руками, не переспрашивая модель.

Формат прост намеренно: строка без знака — заголовок слайда, строка со знаком
— его пункт. Всё, что человек уже умеет печатать, не читая инструкции.
"""

from __future__ import annotations

from dpd.models import PresentationStructure, SlideBody, StructureSlide

BULLET_MARKS = ("-", "–", "—", "*", "•")
"""Знаки, которыми человек помечает пункт. Отказ понимать тире вместо дефиса
выглядел бы придиркой машины: печатают то, что под рукой."""

TITLE_ROLE = "title"
BODY_ROLE = "data"


def structure_from_outline(text: str) -> PresentationStructure:
    """Собрать структуру колоды из плана, написанного человеком.

    Первый слайд получает роль титульного: роль определяет выбор макета, и
    титульный слайд, собранный на контентном макете, выглядел бы как
    оборванный раздел.
    """
    slides: list[StructureSlide] = []
    bullets: list[list[str]] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        mark = next((sign for sign in BULLET_MARKS if line.startswith(sign)), None)
        if mark is not None and slides:
            bullets[-1].append(line[len(mark) :].strip())
            continue

        # Пункт без заголовка выше становится слайдом: потерять введённый
        # человеком текст хуже, чем показать его не в том виде.
        headline = line[len(mark) :].strip() if mark else line
        slides.append(StructureSlide(id=f"s{len(slides) + 1}", role=BODY_ROLE, headline=headline))
        bullets.append([])

    if not slides:
        raise ValueError("план колоды пуст: нужен хотя бы один заголовок")

    return PresentationStructure(
        slides=[
            slide.model_copy(
                update={
                    "role": TITLE_ROLE if index == 0 else slide.role,
                    "body": SlideBody(items=items) if items else None,
                }
            )
            for index, (slide, items) in enumerate(zip(slides, bullets, strict=True))
        ]
    )
