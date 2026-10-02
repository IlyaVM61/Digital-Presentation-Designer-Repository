"""Классификация макетов по структуре слотов.

**Имена макетов ненадёжны.** В VK WorkSpace 11 макетов из 15 называются
«Титульный слайд» — след работы через «Дублировать макет»: имя наследуется
от исходного и назначения не отражает. Определять тип по имени значило бы
считать одиннадцать разных макетов одним и выбирать вёрсткой наугад.

Поэтому тип выводится из геометрии: сколько мест под содержимое и где стоит
заголовок. Это же делает классификацию переносимой на шаблон с именами на
любом языке.

**Исключение — договорённость об именах (T-62, ADR-0008).** Если имя каждого
макета шаблона начинается с префикса назначения (`title`, `section`,
`slide`, `table`, `last`), имя — заявление автора, а не след дублирования, и
назначение берётся из него: геометрия обложки, раздела и финала одинакова,
и варианты ставили обложку на финальный макет.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

from dpd.models import Layout, LayoutFamily, LayoutPurpose

TITLE_BAND = 0.2
"""Доля высоты холста, ниже которой заголовок перестаёт быть «шапкой».

Заголовок в верхней полосе — признак рабочего слайда: место под содержимое
идёт следом. Заголовок, опущенный ниже, обычно занимает композиционный центр,
и такой макет титульный."""


def classify(slots) -> LayoutFamily:
    """Определить тип макета по составу и расположению слотов."""
    if not slots:
        return "blank"

    # Место под содержимое — только слот тела. Номер слайда и колонтитулы
    # делали макет из одного заголовка контентным (найдено прогоном T-43).
    content = [slot for slot in slots if slot.kind == "body"]
    title = next((slot for slot in slots if slot.kind == "title"), None)
    # Без слота заголовка заголовок слайда пропадает: такой макет — последний
    # запасной, а не рабочий (найдено прогоном T-43).
    if title is None:
        return "blank"
    if not content:
        return "section"
    if len(content) >= 2:
        return "split"

    if title.bounds.y >= TITLE_BAND:
        return "title"
    return "content"


def classified(layout: Layout) -> Layout:
    """Проставить макету тип и источник вывода."""
    return layout.model_copy(
        update={"family": classify(layout.slots), "family_source": "structure"}
    )


NAME_PREFIXES: tuple[tuple[str, LayoutPurpose], ...] = (
    ("title", "cover"),
    ("section", "section"),
    ("slide", "regular"),
    ("table", "table"),
    ("last", "closing"),
)
"""Договорённость об именах макетов: префикс имени → назначение.

Префикс сравнивается с учётом регистра: имена макетов редакторов —
«Title Slide», «TITLE_AND_BODY» — пишутся с заглавной и договорённостью не
являются, а одно случайное совпадение и так ничего не решает — нужны все
макеты шаблона."""

PURPOSE_FAMILY: dict[LayoutPurpose, LayoutFamily] = {
    "cover": "title",
    "section": "section",
    "closing": "title",
}
"""Тип макета, следующий из назначения. Обычному слайду и слайду с таблицей
тип по-прежнему даёт геометрия: одно место под содержимое или несколько —
об этом имя не говорит."""


def purpose_of(name: str) -> LayoutPurpose | None:
    """Назначение макета по префиксу имени; `None` — имя вне договорённости."""
    return next((purpose for prefix, purpose in NAME_PREFIXES if name.startswith(prefix)), None)


def named(layouts: list[Layout]) -> list[Layout]:
    """Проставить назначение из имён, если договорённости следуют все макеты.

    Иначе имена не сигнал, и макеты возвращаются как есть — с типом из
    геометрии. Решение принимается по шаблону целиком: в калибровочном
    шаблоне 11 макетов из 15 называются одинаково, и имя одного макета,
    случайно совпавшее с префиксом, ничего не говорит.
    """
    purposes = [purpose_of(layout.name) for layout in layouts]
    if not layouts or None in purposes:
        return layouts
    return [_with_purpose(layout, purpose) for layout, purpose in zip(layouts, purposes)]


def _with_purpose(layout: Layout, purpose: LayoutPurpose) -> Layout:
    family = PURPOSE_FAMILY.get(purpose)
    if family is None:
        # Обычный слайд и слайд с таблицей — рабочие, даже если геометрия
        # сочла иначе: макет из таблицы и картинки без тела она относит к
        # разделам.
        family = layout.family if layout.family in ("content", "split") else "content"
    return layout.model_copy(update={"purpose": purpose, "family": family, "family_source": "name"})
