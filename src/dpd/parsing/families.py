"""Классификация макетов по структуре слотов.

**Имена макетов ненадёжны.** В VK WorkSpace 11 макетов из 15 называются
«Титульный слайд» — след работы через «Дублировать макет»: имя наследуется
от исходного и назначения не отражает. Определять тип по имени значило бы
считать одиннадцать разных макетов одним и выбирать вёрсткой наугад.

Поэтому тип выводится из геометрии: сколько мест под содержимое и где стоит
заголовок. Это же делает классификацию переносимой на шаблон с именами на
любом языке.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

from dpd.models import Layout, LayoutFamily

TITLE_BAND = 0.2
"""Доля высоты холста, ниже которой заголовок перестаёт быть «шапкой».

Заголовок в верхней полосе — признак рабочего слайда: место под содержимое
идёт следом. Заголовок, опущенный ниже, обычно занимает композиционный центр,
и такой макет титульный."""


def classify(slots) -> LayoutFamily:
    """Определить тип макета по составу и расположению слотов."""
    if not slots:
        return "blank"

    content = [slot for slot in slots if slot.kind != "title"]
    if not content:
        return "section"
    if len(content) >= 2:
        return "split"

    title = next((slot for slot in slots if slot.kind == "title"), None)
    if title is not None and title.bounds.y >= TITLE_BAND:
        return "title"
    return "content"


def classified(layout: Layout) -> Layout:
    """Проставить макету тип и источник вывода."""
    return layout.model_copy(
        update={"family": classify(layout.slots), "family_source": "structure"}
    )
