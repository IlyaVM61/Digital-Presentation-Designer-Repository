"""Девять колод промежуточной сдачи одной командой (T-42).

    D:\\venvs\\dpd\\Scripts\\python -m dpd.batch --outline ПЛАН.txt

Матрица — три шаблона из `configs/decks.yaml` на три варианта вёрстки, на
одном контенте (`docs/08-backlog/deliverables.md`). Результат раскладывается
по схеме сдачи: колоды в `decks/` под именами `{слаг}__{вариант}` во всех трёх
форматах, отчёты аудита в `audit/` под теми же именами.

**Прогон на шаблон — обычный `run_pipeline`**, тот же, что за интерфейсом.
Отдельный путь сборки для сдачи значил бы, что комиссия видит не то, что
видит пользователь. Оркестратор называет файлы по имени шаблона; батч
переносит их под имена схемы, а рабочий каталог прогона удаляет.

**Содержание — план колоды, написанный человеком**, в том же формате, что на
странице. Готовой структуры в контент-пакете быть не должно: её строит
генерация по брифу (T-49), и когда она появится, батч получит её вход.
Подменять её правдоподобной заглушкой нельзя.

**Сбой одного шаблона не останавливает остальные.** Он виден по коду
возврата и по сводке, но колоды соседних шаблонов собираются: девять колод
не должны зависеть от самого слабого файла.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from dpd.generation import structure_from_outline
from dpd.models import Finding, PresentationStructure
from dpd.orchestrator import run_pipeline

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATRIX = ROOT / "configs" / "decks.yaml"

FORMATS: tuple[str, ...] = ("pptx", "pdf", "html")
"""Все три обязательны по ТЗ; меньший набор нужен только для отладки."""


@dataclass
class Built:
    """Итог одного шаблона: файлы по варианту и формату или причина сбоя."""

    slug: str
    decks: dict[str, dict[str, Path]] = field(default_factory=dict)
    reports: dict[str, Path] = field(default_factory=dict)
    distinction: list[Finding] = field(default_factory=list)
    seconds: float = 0.0
    error: str | None = None


def load_matrix(path: str | Path = DEFAULT_MATRIX) -> list[tuple[str, Path]]:
    """Шаблоны сдачи в порядке конфига: слаг и путь к файлу."""
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    base = ROOT / config.get("templates_dir", ".")
    return [(entry["slug"], base / entry["file"]) for entry in config["templates"]]


def deck_name(slug: str, variant: str) -> str:
    """Имя колоды по схеме сдачи: `vk-tech__a`."""
    return f"{slug}__{variant.lower()}"


def build_decks(
    matrix: Sequence[tuple[str, Path]],
    structure: PresentationStructure,
    out_root: Path,
    *,
    formats: Sequence[str] = FORMATS,
) -> list[Built]:
    """Собрать все варианты каждого шаблона и разложить их по схеме сдачи."""
    decks_dir, audit_dir = out_root / "decks", out_root / "audit"
    decks_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    results: list[Built] = []
    for slug, template in matrix:
        built = Built(slug=slug)
        started = time.perf_counter()
        print(f"{slug}: собираю по шаблону «{template.name}»…", flush=True)
        # Рабочий каталог — внутри каталога сдачи: перенос в пределах одного
        # диска не копирует файлы, а системный диск не тратится.
        with tempfile.TemporaryDirectory(dir=out_root, prefix=".work-", ignore_cleanup_errors=True) as work:
            try:
                result = run_pipeline(template, structure, work, formats=formats)
            except Exception as error:  # noqa: BLE001 — сбой шаблона идёт в сводку, а не роняет батч
                built.error = f"{type(error).__name__}: {error}"
            else:
                for variant, files in result.variant_exports.items():
                    name = deck_name(slug, variant)
                    built.decks[variant] = {
                        key: path.replace(decks_dir / f"{name}{path.suffix}") for key, path in files.items()
                    }
                    report = audit_dir / f"{name}.report.json"
                    report.write_text(
                        result.reports[variant].model_dump_json(indent=2, by_alias=True), encoding="utf-8"
                    )
                    built.reports[variant] = report
                built.distinction = result.distinction
        built.seconds = time.perf_counter() - started
        print(_summary(built), flush=True)
        results.append(built)
    return results


def _summary(built: Built) -> str:
    if built.error:
        return f"{built.slug}: СБОЙ — {built.error}"
    names = ", ".join(deck_name(built.slug, variant) for variant in built.decks)
    lines = [f"{built.slug}: готово за {built.seconds:.1f} с — {names}"]
    lines += [f"{built.slug}: внимание — {finding.message}" for finding in built.distinction]
    return "\n".join(lines)


def _formats(value: str) -> tuple[str, ...]:
    chosen = tuple(part.strip() for part in value.split(",") if part.strip())
    unknown = sorted(set(chosen) - set(FORMATS))
    if unknown or not chosen:
        raise argparse.ArgumentTypeError(f"неизвестный формат: {', '.join(unknown) or 'пусто'}; есть {', '.join(FORMATS)}")
    return chosen


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dpd.batch", description="Девять колод промежуточной сдачи.")
    parser.add_argument("--outline", type=Path, required=True, help="план колоды: заголовки и пункты со знаком «-»")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(os.environ.get("DPD_OUTPUT_DIR", "D:/dpd-out")) / "out",
        help="каталог сдачи; внутри появятся decks/ и audit/",
    )
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX, help="перечень шаблонов, configs/decks.yaml")
    parser.add_argument("--formats", type=_formats, default=FORMATS, help="через запятую; по умолчанию все три")
    args = parser.parse_args(argv)

    if not args.outline.is_file():
        parser.error(f"план колоды не найден: {args.outline}")
    structure = structure_from_outline(args.outline.read_text(encoding="utf-8"))
    matrix = load_matrix(args.matrix)
    missing = [str(template) for _, template in matrix if not template.is_file()]
    if missing:
        parser.error("нет файлов шаблонов: " + "; ".join(missing))

    started = time.perf_counter()
    results = build_decks(matrix, structure, args.out, formats=args.formats)
    failed = [built.slug for built in results if built.error]
    total = sum(len(built.decks) for built in results)
    print(f"Итого: {total} колод за {time.perf_counter() - started:.1f} с → {args.out}", flush=True)
    if failed:
        print(f"Не собраны: {', '.join(failed)}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
