"""Синхронизация кода в репозиторий второго трека на GitLab (T-76).

Репозиторий второго трека отдельный, и материалы первого трека — шаблоны,
ТЗ, разбор шаблонов, рабочие документы — туда не попадают. Что переносится,
решает список путей `gitlab-sync/paths.txt`; файлы берутся из коммита
исходного репозитория (по умолчанию `HEAD`), только отслеживаемые git, —
черновик рядом уйти не может. Файлы, нужные только репозиторию GitLab,
лежат в `gitlab-sync/overlay/` и кладутся в его корень поверх.

В `main` репозитория GitLab push запрещён: скрипт делает ветку `feature/…`
от свежего `origin/main`, кладёт в неё один коммит и с `--push` отправляет
её, попросив GitLab открыть merge request. Сливает владелец, в браузере.

Пути из списка зеркалируются: файл, исчезнувший из исходного репозитория
или из списка, в ветке удаляется. Файлы вне списка не трогаются. README у
репозитория GitLab свой — `overlay/README.md` (T-78), он встаёт поверх
созданного GitLab. Папка `examples/` есть только в GitLab: примеры —
материалы второго трека, в публичный репозиторий не попадают и кладутся в
ветку отдельным коммитом, руками (handoff, раздел о T-78).

Запуск из корня проекта; целевой каталог — локальный клон репозитория GitLab,
адрес которого в этом репозитории не хранится:

    python gitlab-sync/sync.py --target D:\\dpd-gitlab --push

Вместо `--target` можно задать переменную окружения `DPD_GITLAB_DIR`.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

PATHS = "gitlab-sync/paths.txt"
OVERLAY = "gitlab-sync/overlay/"


class SyncError(Exception):
    """Синхронизация невозможна; рабочее дерево клона не тронуто."""


@dataclass
class Result:
    branch: str
    commit: str | None
    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    push_log: str = ""


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    done = subprocess.run(["git", *args], cwd=repo, input=data, capture_output=True)
    if done.returncode != 0:
        message = done.stderr.decode("utf-8", "replace").strip()
        raise SyncError(f"git {' '.join(args)}: {message}")
    return done.stdout


def nul_list(data: bytes) -> list[str]:
    return [item.decode("utf-8") for item in data.split(b"\0") if item]


def parse_paths(text: str) -> tuple[list[str], list[str]]:
    """Строки списка: путь файла, каталог со слешем на конце, `!` — исключение."""
    include: list[str] = []
    exclude: list[str] = []
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith("!"):
            exclude.append(entry[1:])
        else:
            include.append(entry)
    return include, exclude


def matches(path: str, entries: list[str]) -> bool:
    return any(path.startswith(entry) if entry.endswith("/") else path == entry for entry in entries)


def select(files: list[str], include: list[str], exclude: list[str]) -> list[str]:
    return sorted(path for path in files if matches(path, include) and not matches(path, exclude))


def read_blobs(repo: Path, ref: str, paths: list[str]) -> dict[str, bytes]:
    request = "".join(f"{ref}:{path}\n" for path in paths).encode("utf-8")
    out = git(repo, "cat-file", "--batch", data=request)
    blobs: dict[str, bytes] = {}
    position = 0
    for path in paths:
        header_end = out.index(b"\n", position)
        size = int(out[position:header_end].split()[2])
        start = header_end + 1
        blobs[path] = out[start : start + size]
        position = start + size + 1
    return blobs


def plan(source: Path, ref: str) -> tuple[dict[str, bytes], list[str]]:
    """Что должно лежать в ветке: путь в GitLab → содержимое, и список путей."""
    tracked = nul_list(git(source, "ls-tree", "-r", "-z", "--name-only", "--full-tree", ref))
    if PATHS not in tracked:
        raise SyncError(f"в {ref} нет {PATHS}")
    include, exclude = parse_paths(read_blobs(source, ref, [PATHS])[PATHS].decode("utf-8"))
    chosen = select(tracked, include, exclude)
    overlay = [path for path in tracked if path.startswith(OVERLAY)]
    blobs = read_blobs(source, ref, chosen + overlay)
    files = {path: blobs[path] for path in chosen}
    for path in overlay:
        files[path[len(OVERLAY) :]] = blobs[path]
    return files, include


def sync(
    source: Path,
    target: Path,
    branch: str,
    *,
    ref: str = "HEAD",
    base: str = "main",
    push: bool = False,
) -> Result:
    if not branch.startswith("feature/"):
        raise SyncError(f"ветка должна начинаться с feature/: push в {branch} запрещён")
    if git(target, "status", "--porcelain").strip():
        raise SyncError(f"в {target} есть незакоммиченные изменения")

    files, include = plan(source, ref)
    source_commit = git(source, "rev-parse", ref).decode().strip()

    git(target, "fetch", "-q", "origin")
    git(target, "checkout", "-q", "-B", branch, f"origin/{base}")

    existing = nul_list(git(target, "ls-files", "-z"))
    removed = sorted(path for path in existing if path not in files and matches(path, include))
    if removed:
        git(target, "rm", "-q", "--pathspec-from-file=-", "--pathspec-file-nul", data=b"\0".join(p.encode() for p in removed))

    for path, content in files.items():
        destination = target / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    # Список — единственный судья: всё, что в нём, уже лежит в исходном
    # репозитории, поэтому `.gitignore` клона его не перебивает.
    git(target, "add", "-f", "--pathspec-from-file=-", "--pathspec-file-nul", data=b"\0".join(p.encode() for p in files))

    result = Result(branch=branch, commit=None, written=sorted(files), removed=removed)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=target).returncode == 0:
        return result

    git(target, "commit", "-q", "-m", f"Синхронизация с исходным коммитом {source_commit[:12]}")
    result.commit = git(target, "rev-parse", "HEAD").decode().strip()

    if push:
        title = f"Синхронизация с исходным коммитом {source_commit[:12]}"
        done = subprocess.run(
            [
                "git", "push", "-u", "origin", branch,
                "-o", "merge_request.create",
                "-o", f"merge_request.target={base}",
                "-o", f"merge_request.title={title}",
            ],
            cwd=target,
            capture_output=True,
        )
        result.push_log = done.stderr.decode("utf-8", "replace")
        if done.returncode != 0:
            raise SyncError(f"push не прошёл:\n{result.push_log}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", default=os.environ.get("DPD_GITLAB_DIR"), help="локальный клон репозитория GitLab")
    parser.add_argument("--branch", default=f"feature/sync-{datetime.now():%Y%m%d-%H%M}")
    parser.add_argument("--ref", default="HEAD", help="коммит исходного репозитория")
    parser.add_argument("--base", default="main", help="ветка, в которую пойдёт merge request")
    parser.add_argument("--push", action="store_true", help="отправить ветку и открыть merge request")
    args = parser.parse_args()
    if not args.target:
        parser.error("нужен --target или переменная DPD_GITLAB_DIR")

    # Вывод в канал Windows кодирует в cp1251 — как в dpd.batch.
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")

    source = Path(__file__).resolve().parents[1]
    try:
        result = sync(source, Path(args.target), args.branch, ref=args.ref, base=args.base, push=args.push)
    except SyncError as error:
        print(f"Синхронизация остановлена: {error}", file=sys.stderr)
        return 1

    if result.commit is None:
        print(f"Изменений нет: {args.base} в GitLab совпадает с исходным коммитом.")
        return 0
    print(f"Ветка {result.branch}: файлов {len(result.written)}, удалено {len(result.removed)}, коммит {result.commit[:12]}.")
    if result.push_log:
        print(result.push_log)
    elif not args.push:
        print("Не отправлено: проверьте ветку в клоне и запустите ещё раз с --push.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
