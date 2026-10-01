"""Контент-пакет — вход слоя генерации: бриф и фактура.

Формат зафиксирован тем, что уже лежит в `assets/content-pack/`: два файла
Markdown. `brief.md` — назначение, аудитория, цель, объём; `content.md` —
тезисы, цифры, таблицы, цитаты. Готовой структуры слайдов в пакете нет:
её строит генерация, это и есть проверяемая функциональность.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

BRIEF_FILE = "brief.md"
CONTENT_FILE = "content.md"


@dataclass(frozen=True)
class ContentPack:
    """Тексты пакета ровно в том виде, в каком их написал человек."""

    brief: str
    content: str


def load_content_pack(directory: Path) -> ContentPack:
    """Прочитать пакет из каталога; без любого из двух файлов — ошибка.

    Пакет без фактуры не даёт модели ни одного числа, и колода вышла бы
    выдуманной; без брифа неизвестны назначение и аудитория.
    """
    texts = {}
    for name in (BRIEF_FILE, CONTENT_FILE):
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(f"в контент-пакете {directory} нет файла {name}")
        texts[name] = path.read_text(encoding="utf-8").strip()
    return ContentPack(brief=texts[BRIEF_FILE], content=texts[CONTENT_FILE])
