"""Девять колод промежуточной сдачи одной командой (T-42).

    D:\\venvs\\dpd\\Scripts\\python -m dpd.batch

Матрица — три шаблона из `configs/decks.yaml` на три варианта вёрстки, на
одном контенте (`docs/08-backlog/deliverables.md`). Результат раскладывается
по схеме сдачи: колоды в `decks/` под именами `{слаг}__{вариант}` во всех трёх
форматах, отчёты аудита в `audit/` под теми же именами.

**Прогон на шаблон — обычный `run_pipeline`**, тот же, что за интерфейсом.
Отдельный путь сборки для сдачи значил бы, что комиссия видит не то, что
видит пользователь. Оркестратор называет файлы по имени шаблона; батч
переносит их под имена схемы, а рабочий каталог прогона удаляет.

**Содержание генерирует модель по контент-пакету** (`assets/content-pack/`
по умолчанию): структура колоды по брифу (T-49), затем содержание слайдов
со ссылками на источник (T-50). Колода генерируется один раз, и все девять
собираются из неё — «на одном контенте» по ТЗ. Готовой структуры в пакете
нет, её строит генерация. План, написанный человеком, остаётся входом
`--outline` для прогона без модели: детерминированный контур работает
офлайн (NFR-7), но колодами сдачи такой прогон не считается.

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

from dpd.generation import generate_presentation, load_content_pack, structure_from_outline
from dpd.generation.content_pack import BRIEF_FILE, CONTENT_FILE
from dpd.llm import ModelClient, ModelError, PromptSet, build_client, load_prompts, load_settings
from dpd.models import Finding, PresentationStructure
from dpd.orchestrator import run_pipeline

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATRIX = ROOT / "configs" / "decks.yaml"
DEFAULT_PACK = ROOT / "assets" / "content-pack"

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


def model_client(prompts: PromptSet) -> ModelClient:
    """Клиент LLM по `configs/models.yaml` и `.env`."""
    return build_client(load_settings("llm"), prompts)


def generate_from_pack(directory: Path) -> PresentationStructure:
    """Колода по контент-пакету: структура и содержание слайдов от модели."""
    pack = load_content_pack(directory)
    prompts = load_prompts()
    started = time.perf_counter()
    print(f"Генерирую колоду по контент-пакету {directory}…", flush=True)
    structure = generate_presentation(pack, model_client(prompts), prompts)
    print(f"Колода из {len(structure.slides)} слайдов готова за {time.perf_counter() - started:.1f} с", flush=True)
    return structure


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
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--content-pack",
        type=Path,
        default=DEFAULT_PACK,
        help="каталог с brief.md и content.md; по умолчанию assets/content-pack",
    )
    source.add_argument("--outline", type=Path, help="план колоды без модели: заголовки и пункты со знаком «-»")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(os.environ.get("DPD_OUTPUT_DIR", "D:/dpd-out")) / "out",
        help="каталог сдачи; внутри появятся decks/ и audit/",
    )
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX, help="перечень шаблонов, configs/decks.yaml")
    parser.add_argument("--formats", type=_formats, default=FORMATS, help="через запятую; по умолчанию все три")
    args = parser.parse_args(argv)

    # Вывод в канал Windows кодирует в cp1251: кириллица приходит в журнал
    # кракозябрами, а символ вне кодировки ронял команду уже после того, как
    # все колоды собраны. Терминал Python и так пишет в Юникоде.
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Вход проверяется до запроса к модели: генерация стоит денег и десятков секунд.
    if args.outline is not None and not args.outline.is_file():
        parser.error(f"план колоды не найден: {args.outline}")
    if args.outline is None:
        absent = [name for name in (BRIEF_FILE, CONTENT_FILE) if not (args.content_pack / name).is_file()]
        if absent:
            parser.error(f"в контент-пакете {args.content_pack} нет {', '.join(absent)}")
    matrix = load_matrix(args.matrix)
    missing = [str(template) for _, template in matrix if not template.is_file()]
    if missing:
        parser.error("нет файлов шаблонов: " + "; ".join(missing))

    if args.outline is not None:
        structure = structure_from_outline(args.outline.read_text(encoding="utf-8"))
    else:
        try:
            structure = generate_from_pack(args.content_pack)
        except ModelError as error:
            print(f"Колода не сгенерирована: {error}", flush=True)
            return 1

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
