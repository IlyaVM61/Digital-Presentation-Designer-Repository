"""T-76: код второго трека едет в его репозиторий GitLab скриптом синхронизации.

Репозиторий второго трека отдельный: туда попадает код, но не материалы
первого трека — шаблоны, ТЗ и разбор шаблонов VK, рабочие документы. Что
переносится, решает список путей `gitlab-sync/paths.txt`, а берутся только
отслеживаемые git файлы из коммита: черновик, лежащий рядом, уйти не может.
Файлы, которые нужны только репозиторию GitLab, — `.gitignore` и настройки
редактора с выключенным GitDoc — лежат в `gitlab-sync/overlay/`.

В `main` второго репозитория push запрещён: скрипт кладёт коммит в ветку
`feature/…` от свежего `main`, а слияние — через merge request.

Критерий приёмки: в ветку попадают ровно файлы по списку и файлы overlay;
неотслеживаемые и исключённые не попадают; файл, убранный из исходного
репозитория или из списка, удаляется; файлы вне списка (README, который
GitLab создал сам) не трогаются; повтор без изменений не создаёт коммита;
ветка `main` и грязное рабочее дерево отклоняются; `main` удалённого
репозитория не меняется.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "gitlab-sync"


def load_sync():
    spec = importlib.util.spec_from_file_location("gitlab_sync", SYNC / "sync.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass ищет свой модуль в sys.modules
    spec.loader.exec_module(module)
    return module


sync = load_sync()


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8"
    )
    return done.stdout


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture(autouse=True)
def identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Тест")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Исходный репозиторий, «GitLab» с README в main и его клон."""
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    write(source, "gitlab-sync/paths.txt", "# код\nsrc/\nscripts/\n!scripts/private.py\npyproject.toml\n")
    write(source, "gitlab-sync/overlay/.gitignore", ".env\n")
    write(source, "gitlab-sync/overlay/.vscode/settings.json", '{"gitdoc.enabled": false}\n')
    write(source, "src/pkg/a.py", "A = 1\n")
    write(source, "src/pkg/шаблон с пробелом.txt", "текст\n")
    write(source, "scripts/tool.py", "print('tool')\n")
    write(source, "scripts/private.py", "print('private')\n")
    write(source, "docs/notes.md", "первый трек\n")
    write(source, "pyproject.toml", "[project]\nname = 'x'\n")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "начало")
    write(source, "src/pkg/draft.py", "черновик\n")  # не отслеживается

    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(remote, "config", "receive.advertisePushOptions", "true")
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(remote), str(seed))
    write(seed, "README.md", "# Созданный GitLab\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "README")
    git(seed, "push", "-q", "origin", "HEAD:main")

    target = tmp_path / "target"
    git(tmp_path, "clone", "-q", str(remote), str(target))
    return source, remote, target


def tree(repo: Path, ref: str = "HEAD") -> set[str]:
    return set(git(repo, "ls-tree", "-r", "--name-only", "-z", ref).split("\0")) - {""}


def test_branch_gets_listed_files_and_overlay(repos: tuple[Path, Path, Path]) -> None:
    source, remote, target = repos

    result = sync.sync(source, target, "feature/sync-1", push=True)

    assert result.commit is not None
    assert tree(remote, "feature/sync-1") == {
        "README.md",
        ".gitignore",
        ".vscode/settings.json",
        "pyproject.toml",
        "scripts/tool.py",
        "src/pkg/a.py",
        "src/pkg/шаблон с пробелом.txt",
    }
    assert tree(remote, "main") == {"README.md"}, "main удалённого репозитория изменился"
    shown = git(remote, "show", "feature/sync-1:src/pkg/a.py")
    assert shown == "A = 1\n"
    assert git(source, "rev-parse", "HEAD").strip()[:12] in git(target, "log", "-1", "--format=%s")


def test_removed_file_disappears_and_foreign_files_stay(repos: tuple[Path, Path, Path]) -> None:
    source, _, target = repos
    sync.sync(source, target, "feature/sync-1")
    git(target, "checkout", "-q", "main")  # в настоящем потоке ветку сливает MR
    git(target, "merge", "-q", "--ff-only", "feature/sync-1")
    git(target, "push", "-q", "origin", "main")

    git(source, "rm", "-q", "scripts/tool.py")
    write(source, "gitlab-sync/paths.txt", "src/\nscripts/\npyproject.toml\n!src/pkg/a.py\n")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "убрали")

    result = sync.sync(source, target, "feature/sync-2")

    files = tree(target)
    assert result.removed == ["scripts/tool.py", "src/pkg/a.py"]
    assert "scripts/tool.py" not in files
    assert "src/pkg/a.py" not in files
    assert "scripts/private.py" in files, "раз исключение снято, файл должен приехать"
    assert "README.md" in files, "файл вне списка путей не трогается"


def test_nothing_to_sync_makes_no_commit(repos: tuple[Path, Path, Path]) -> None:
    source, _, target = repos
    sync.sync(source, target, "feature/sync-1", push=True)
    git(target, "checkout", "-q", "main")
    git(target, "merge", "-q", "--ff-only", "feature/sync-1")
    git(target, "push", "-q", "origin", "main")

    result = sync.sync(source, target, "feature/sync-2")

    assert result.commit is None
    assert git(target, "rev-parse", "HEAD") == git(target, "rev-parse", "origin/main")


def test_main_branch_is_refused(repos: tuple[Path, Path, Path]) -> None:
    source, _, target = repos
    with pytest.raises(sync.SyncError, match="feature/"):
        sync.sync(source, target, "main")


def test_dirty_target_is_refused(repos: tuple[Path, Path, Path]) -> None:
    source, _, target = repos
    write(target, "README.md", "правка руками\n")
    with pytest.raises(sync.SyncError, match="незакоммиченные"):
        sync.sync(source, target, "feature/sync-1")


# --- Настоящий список путей проекта ------------------------------------------


def project_selection() -> list[str]:
    tracked = git(ROOT, "ls-files", "-z").split("\0")
    include, exclude = sync.parse_paths((SYNC / "paths.txt").read_text(encoding="utf-8"))
    return sync.select([path for path in tracked if path], include, exclude)


def test_project_list_carries_code_but_not_track_one() -> None:
    chosen = project_selection()

    for needed in (
        "pyproject.toml",
        "src/dpd/orchestrator.py",
        "ui/app.py",
        "prompts/CHANGELOG.md",
        "configs/models.yaml",
        "deploy/deploy.sh",
        "tests/conftest.py",
        "ARCHITECTURE.md",
    ):
        assert needed in chosen, needed
    for path in chosen:
        assert not path.startswith(("docs/", ".claude/", "gitlab-sync/")), path
        assert path not in {"CLAUDE.md", "AGENTS.md", "README.md", "tests/test_sync_gitlab.py"}, path
        assert not path.startswith("assets/templates/calibration/"), path
        if path.startswith("assets/templates/corporate/"):
            assert path == "assets/templates/corporate/README.md", path


def test_every_entry_of_project_list_matches_a_tracked_file() -> None:
    """Опечатка в списке молча оставила бы GitLab без нужной папки."""
    tracked = [path for path in git(ROOT, "ls-files", "-z").split("\0") if path]
    include, exclude = sync.parse_paths((SYNC / "paths.txt").read_text(encoding="utf-8"))
    for entry in include + exclude:
        assert any(sync.matches(path, [entry]) for path in tracked), entry


def test_overlay_turns_gitdoc_off_and_ignores_secrets() -> None:
    settings = json.loads((SYNC / "overlay/.vscode/settings.json").read_text(encoding="utf-8"))
    assert settings["gitdoc.enabled"] is False

    ignored = (SYNC / "overlay/.gitignore").read_text(encoding="utf-8").splitlines()
    for rule in (".env", "assets/templates/corporate/*", "!assets/templates/corporate/README.md"):
        assert rule in ignored, rule
