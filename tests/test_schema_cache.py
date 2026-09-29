"""T-19: кеширование схемы шаблона по хешу файла.

Критерий приёмки задачи: повторный разбор того же файла не выполняется.

Разбор шаблона занимает доли секунды, но выполняется он при каждом прогоне,
а девять демонстрационных колод собираются на трёх шаблонах — двадцать семь
разборов одних и тех же файлов. Кеш убирает их полностью.

Ключ кеша включает версию парсера. Без неё после правки разбора система
продолжила бы отдавать схему, собранную прежним кодом, — дефект, который
проявился бы как «изменения не применяются» и искался бы часами.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import dpd.parsing.template_parser as parser_module
from dpd.parsing import parse_template
from dpd.parsing.cache import cache_path, clear_cache

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"
TEMPLATE = "VK Tech шаблон.pptx"


@pytest.fixture
def template_path() -> Path:
    path = CALIBRATION / TEMPLATE
    if not path.exists():
        pytest.skip(f"нет калибровочного шаблона {TEMPLATE}")
    return path


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch) -> Path:
    """Изолированный кеш: тест не должен зависеть от прошлых прогонов."""
    directory = tmp_path / "cache"
    monkeypatch.setenv("DPD_CACHE_DIR", str(directory))
    return directory


def test_first_parse_writes_the_cache(template_path: Path, cache_dir: Path) -> None:
    schema = parse_template(template_path)
    assert cache_path(template_path).is_file()
    assert schema.layouts


def test_second_parse_does_not_open_the_file(template_path: Path, cache_dir: Path, monkeypatch) -> None:
    """Критерий приёмки: повторный разбор не выполняется."""
    parse_template(template_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("файл открыт повторно — кеш не сработал")

    monkeypatch.setattr(parser_module, "Presentation", forbidden)
    schema = parse_template(template_path)
    assert schema.layouts


def test_cached_schema_equals_the_original(template_path: Path, cache_dir: Path) -> None:
    first = parse_template(template_path)
    second = parse_template(template_path)
    assert first == second


def test_parser_version_is_part_of_the_key(template_path: Path, cache_dir: Path, monkeypatch) -> None:
    """После правки парсера кеш обязан промахнуться, а не отдать старое."""
    parse_template(template_path)
    old_key = cache_path(template_path)

    monkeypatch.setattr(parser_module, "PARSER_VERSION", "9.9.9")
    monkeypatch.setattr("dpd.parsing.cache.PARSER_VERSION", "9.9.9")
    assert cache_path(template_path) != old_key


def test_different_files_get_different_keys(template_path: Path, cache_dir: Path, tmp_path: Path) -> None:
    other = tmp_path / "other.pptx"
    other.write_bytes(template_path.read_bytes() + b"\x00")
    assert cache_path(template_path) != cache_path(other)


def test_corrupted_cache_falls_back_to_parsing(template_path: Path, cache_dir: Path) -> None:
    """Испорченный кеш не должен ронять прогон.

    Файл кеша живёт в каталоге результатов и может быть повреждён чем
    угодно — прерванной записью, чисткой диска. Отказ разбирать шаблон
    из-за этого был бы хуже самой проблемы.
    """
    parse_template(template_path)
    cache_path(template_path).write_text("не json", encoding="utf-8")
    assert parse_template(template_path).layouts


def test_cache_can_be_cleared(template_path: Path, cache_dir: Path) -> None:
    parse_template(template_path)
    assert cache_path(template_path).is_file()
    clear_cache()
    assert not cache_path(template_path).is_file()


def test_cache_can_be_bypassed(template_path: Path, cache_dir: Path, monkeypatch) -> None:
    """Возможность обойти кеш нужна для отладки разбора."""
    parse_template(template_path)
    opened: list[str] = []
    original = parser_module.Presentation

    def counting(path):
        opened.append(str(path))
        return original(path)

    monkeypatch.setattr(parser_module, "Presentation", counting)
    parse_template(template_path, use_cache=False)
    assert opened, "обход кеша не сработал"
