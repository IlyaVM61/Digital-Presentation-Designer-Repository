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
import re
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


# --- README второго трека (T-78) ---------------------------------------------
#
# У репозитория GitLab свой README: он лежит в overlay и встаёт в корень
# поверх того, что создал GitLab. Читают его эксперты второго трека, поэтому
# он не ссылается ни на рабочие документы `docs/`, ни на публичный
# репозиторий, а адрес сервиса и пароль живут только на сервере и на
# платформе сдачи. Примеры — папка `examples/` только репозитория GitLab: она
# кладётся отдельным коммитом из локальных материалов второго трека, мимо
# публичного репозитория, и синхронизация её не трогает.

README = SYNC / "overlay/README.md"
EXAMPLES_SOURCE = ROOT / "assets/templates/corporate/examples"

# Путь в обратных кавычках: каталог проекта или файл в корне.
REPO_PATH = re.compile(
    r"`((?:src|ui|tests|prompts|configs|scripts|deploy|assets|docs|examples)/[^`\s]*"
    r"|[A-Z][A-Z_]*\.md|pyproject\.toml|\.env\.example)`"
)
# Внешние адреса, которые README вправе называть: провайдер по умолчанию и
# программы для установки.
PUBLIC_HOSTS = {"openrouter.ai", "www.libreoffice.org", "www.python.org"}


def track_two_readme() -> str:
    return README.read_text(encoding="utf-8")


def test_overlay_readme_replaces_the_one_gitlab_created(repos: tuple[Path, Path, Path]) -> None:
    source, remote, target = repos
    write(source, "gitlab-sync/overlay/README.md", "# Второй трек\n")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "README второго трека")

    sync.sync(source, target, "feature/sync-1", push=True)

    assert git(remote, "show", "feature/sync-1:README.md") == "# Второй трек\n"


def test_track_two_readme_covers_the_task() -> None:
    text = track_two_readme()
    for heading in ("Сервис по ссылке", "Запуск", "Примеры", "Подготовка шаблона", "Замена модели", "Ограничения"):
        assert re.search(rf"^##+ .*{heading}", text, re.MULTILINE), heading


def test_track_two_readme_names_the_layout_prefixes_the_parser_knows() -> None:
    from dpd.parsing.families import NAME_PREFIXES

    text = track_two_readme()
    for prefix, _ in NAME_PREFIXES:
        assert f"`{prefix}" in text, prefix


def test_model_is_replaced_by_the_variables_the_readme_names(tmp_path: Path) -> None:
    from dpd.llm.settings import load_settings

    text = track_two_readme()
    for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "VLM_BASE_URL", "VLM_MODEL"):
        assert f"`{name}" in text, name

    environ = {"LLM_BASE_URL": "https://models.example/v1", "LLM_MODEL": "другая/модель", "LLM_API_KEY": "k"}
    settings = load_settings("llm", environ=environ, env_file=tmp_path / "нет.env")
    assert (settings.base_url, settings.model, settings.api_key) == ("https://models.example/v1", "другая/модель", "k")


def test_track_two_readme_points_only_to_files_that_reach_gitlab() -> None:
    files = set(project_selection()) | {
        path.relative_to(SYNC / "overlay").as_posix() for path in (SYNC / "overlay").rglob("*") if path.is_file()
    }
    for path in REPO_PATH.findall(track_two_readme()):
        if path.startswith("examples/"):
            if EXAMPLES_SOURCE.is_dir():  # материалы второго трека есть только на машине владельца
                assert (EXAMPLES_SOURCE / path.removeprefix("examples/")).exists(), path
            continue
        assert any(file == path or file.startswith(path.rstrip("/") + "/") for file in files), path


def test_track_two_readme_holds_no_server_address() -> None:
    text = track_two_readme()
    hosts = set(re.findall(r"https?://([^/\s)`>]+)", text))
    assert hosts <= PUBLIC_HOSTS, hosts - PUBLIC_HOSTS
    assert "sslip.io" not in text
