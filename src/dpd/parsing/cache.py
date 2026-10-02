"""Кеш разобранных схем шаблонов.

Разбор шаблона занимает доли секунды, но выполняется при каждом прогоне, а
девять демонстрационных колод собираются на трёх шаблонах — двадцать семь
разборов одних и тех же файлов. Кеш убирает их полностью.

**Ключ включает версию парсера.** Без неё после правки разбора система
продолжила бы отдавать схему, собранную прежним кодом: дефект проявился бы
как «изменения не применяются» и искался бы часами.

Кеш живёт в каталоге результатов, а не рядом с проектом: он растёт с каждым
новым шаблоном, а на системном диске места мало.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from dpd.models import TemplateSchema

PARSER_VERSION = "0.5.0"
"""Версия разбора. Повышается при изменении того, что парсер кладёт в схему."""


def cache_dir() -> Path:
    """Каталог кеша: `DPD_CACHE_DIR`, иначе подкаталог результатов прогона."""
    configured = os.environ.get("DPD_CACHE_DIR")
    if configured:
        return Path(configured)
    return Path(os.environ.get("DPD_OUTPUT_DIR", "D:/dpd-out")) / "cache" / "schemas"


def cache_path(template_path: str | Path) -> Path:
    """Файл кеша для этого шаблона и этой версии парсера."""
    digest = _file_hash(Path(template_path))
    return cache_dir() / f"{digest}-{PARSER_VERSION}.json"


def load(template_path: str | Path) -> TemplateSchema | None:
    """Прочитать схему из кеша.

    Испорченный файл кеша не должен ронять прогон: он живёт в каталоге
    результатов и может пострадать от прерванной записи или чистки диска.
    Отказ разобрать шаблон из-за этого был бы хуже самой проблемы.
    """
    path = cache_path(template_path)
    if not path.is_file():
        return None
    try:
        return TemplateSchema.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def store(template_path: str | Path, schema: TemplateSchema) -> None:
    """Сохранить схему в кеш. Неудача записи прогон не прерывает."""
    path = cache_path(template_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(schema.model_dump_json(by_alias=True), encoding="utf-8")
    except OSError:
        return


def clear_cache() -> None:
    """Удалить кеш целиком — нужно при отладке разбора."""
    shutil.rmtree(cache_dir(), ignore_errors=True)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
