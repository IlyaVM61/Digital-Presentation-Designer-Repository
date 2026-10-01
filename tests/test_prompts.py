"""T-48: загрузка и версионирование промптов из `prompts/`.

Промптам в коде не место — прямое требование ТЗ (п. 2.4) и приёмки BA-10.
Каждый файл несёт заголовок YAML с версией, версия набора ведётся в
`prompts/CHANGELOG.md`, и обе попадают в `AuditReport`: иначе утверждение о
воспроизводимости недетерминированной части непроверяемо.

**Версии мало, поэтому в отчёте есть и хеш.** Правку текста без подъёма
версии человек сделает обязательно; хеш содержимого её выдаёт.

**Пустой файл — промпт, который ещё не написан.** В `prompts/` лежат
заготовки для задач, которые впереди. Загрузка их пропускает, а обращение к
такому промпту — громкая ошибка с именем файла, а не пустой системный промпт,
молча ушедший модели.

**«В коде нет промптов» проверяется поиском по исходникам**, как требует
архитектура. Детектор откалиброван на настоящих промптах: каждый из них,
вставленный в код строкой, он обязан поймать — иначе зелёный тест ничего не
доказывает.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import httpx
import pytest

from dpd.llm import ModelSettings, PromptError, build_client, load_prompts

ROOT = Path(__file__).resolve().parents[1]

REPAIR = """---
name: response-repair
version: 3
purpose: просьба исправить ответ
placeholders:
  error: перечень ошибок
---
Ответ не прошёл проверку.

Ошибки: {error}

Верни исправленный JSON. Пример: {"title": "…"}
"""


def write(root: Path, files: dict[str, str], changelog: str | None = "## 2 — 2026-10-01\n\n## 1\n") -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if changelog is not None:
        (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    return root


# --- Разбор файла ----------------------------------------------------------


def test_header_gives_version_and_body_is_the_prompt(tmp_path: Path) -> None:
    prompts = load_prompts(write(tmp_path, {"skills/response-repair.md": REPAIR}))

    prompt = prompts.get("skills/response-repair")
    assert prompt.version == "3"
    assert prompt.placeholders == ("error",)
    assert prompt.text.startswith("Ответ не прошёл проверку.")
    assert "version:" not in prompt.text, "заголовок YAML ушёл бы модели"


def test_windows_line_endings_and_bom_do_not_break_the_header(tmp_path: Path) -> None:
    path = tmp_path / "skills" / "response-repair.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xef\xbb\xbf" + REPAIR.replace("\n", "\r\n").encode("utf-8"))
    write(tmp_path, {})

    assert load_prompts(tmp_path).get("skills/response-repair").version == "3"


@pytest.mark.parametrize(
    "text",
    [
        "Верни JSON.",
        "---\nname: x\n---\nВерни JSON.",
        "---\nversion: 1\nВерни JSON.",
        "---\nversion: [1, 2]\n---\nВерни JSON.",
        "---\nversion: 1\n---\n",
    ],
    ids=["без заголовка", "без версии", "заголовок не закрыт", "версия не строка", "пустое тело"],
)
def test_broken_prompt_is_a_loud_error_naming_the_file(tmp_path: Path, text: str) -> None:
    write(tmp_path, {"skills/broken.md": text})

    with pytest.raises(PromptError, match="broken.md"):
        load_prompts(tmp_path)


def test_declared_placeholder_missing_from_body_is_an_error(tmp_path: Path) -> None:
    text = "---\nversion: 1\nplaceholders:\n  error: ошибки\n---\nИсправь ответ."
    write(tmp_path, {"skills/repair.md": text})

    with pytest.raises(PromptError, match=r"\{error\}"):
        load_prompts(tmp_path)


def test_empty_file_is_skipped_but_asking_for_it_is_loud(tmp_path: Path) -> None:
    prompts = load_prompts(write(tmp_path, {"skills/response-repair.md": REPAIR, "skills/outline-generator.md": ""}))

    assert "skills/outline-generator" not in prompts.versions()
    with pytest.raises(PromptError, match="outline-generator.md.*не написан"):
        prompts.get("skills/outline-generator")
    with pytest.raises(PromptError, match="нет"):
        prompts.get("skills/unknown")


def test_readme_and_changelog_are_not_prompts(tmp_path: Path) -> None:
    prompts = load_prompts(write(tmp_path, {"README.md": "# Промпты\n", "skills/response-repair.md": REPAIR}))

    assert set(prompts.versions()) == {"set", "digest", "skills/response-repair"}


# --- Подстановка -----------------------------------------------------------


def test_render_fills_declared_placeholders_and_leaves_other_braces(tmp_path: Path) -> None:
    prompt = load_prompts(write(tmp_path, {"skills/response-repair.md": REPAIR})).get("skills/response-repair")

    text = prompt.render(error="title: обязательное поле")

    assert "Ошибки: title: обязательное поле" in text
    assert '{"title": "…"}' in text, "фигурные скобки примера JSON не подстановка"


@pytest.mark.parametrize("values", [{}, {"error": "x", "extra": "y"}], ids=["не хватает", "лишнее"])
def test_render_demands_exactly_the_declared_values(tmp_path: Path, values: dict[str, str]) -> None:
    prompt = load_prompts(write(tmp_path, {"skills/response-repair.md": REPAIR})).get("skills/response-repair")

    with pytest.raises(PromptError):
        prompt.render(**values)


# --- Версия набора ---------------------------------------------------------


def test_set_version_is_the_latest_changelog_entry(tmp_path: Path) -> None:
    versions = load_prompts(write(tmp_path, {"skills/response-repair.md": REPAIR})).versions()

    assert versions["set"] == "2"
    assert versions["skills/response-repair"] == "3"


@pytest.mark.parametrize("changelog", [None, "# Журнал\n\nЗаписей нет.\n"], ids=["нет файла", "нет записи"])
def test_set_without_changelog_version_is_an_error(tmp_path: Path, changelog: str | None) -> None:
    write(tmp_path, {"skills/response-repair.md": REPAIR}, changelog=changelog)

    with pytest.raises(PromptError, match="CHANGELOG"):
        load_prompts(tmp_path)


def test_digest_exposes_an_edit_made_without_a_version_bump(tmp_path: Path) -> None:
    first = load_prompts(write(tmp_path / "a", {"skills/response-repair.md": REPAIR})).versions()
    edited = REPAIR.replace("Верни исправленный JSON.", "Верни только JSON.")
    second = load_prompts(write(tmp_path / "b", {"skills/response-repair.md": edited})).versions()

    assert first["skills/response-repair"] == second["skills/response-repair"]
    assert first["digest"] != second["digest"]


# --- Набор репозитория -----------------------------------------------------


def test_repository_prompts_load_with_versions() -> None:
    prompts = load_prompts()

    versions = prompts.versions()
    assert versions["set"]
    assert versions["skills/response-repair"] == "1"
    assert prompts.get("skills/response-repair").placeholders == ("error",)


def test_every_written_prompt_is_in_the_changelog() -> None:
    """Подъём версии без записи в журнал — та же правка без следа."""
    changelog = (ROOT / "prompts" / "CHANGELOG.md").read_text(encoding="utf-8")

    for key, version in load_prompts().versions().items():
        if key in {"set", "digest"}:
            continue
        assert f"`{key}` v{version}" in changelog, f"нет записи о {key} версии {version} в prompts/CHANGELOG.md"


def test_client_takes_the_repair_request_from_the_prompt_file() -> None:
    prompts = load_prompts()
    settings = ModelSettings(
        base_url="https://provider.test/v1",
        api_key="test-key",
        model="test/model",
        temperature=0.0,
        seed=None,
        max_tokens=100,
        timeout_sec=5.0,
        max_attempts=2,
        retry_delay_sec=0.0,
    )

    client = build_client(settings, prompts, transport=httpx.MockTransport(lambda _: httpx.Response(500)))

    assert client.repair_prompt == prompts.get("skills/response-repair").text


# --- В коде нет промптов ---------------------------------------------------

PROMPT_MARKERS = re.compile(
    r"\bВерни\b|\bОтветь\b|\bОтвечай\b|\bТы\s+—|\bYou are\b|\bRespond\b|\bReturn only\b",
    re.IGNORECASE,
)
"""Обращение к модели. Список не исчерпывающий, но откалиброван: каждый
настоящий промпт им ловится (тест ниже), и новый промпт, который он не
ловит, роняет калибровку — тогда список расширяется."""


def prompt_like_strings(source: str) -> list[str]:
    """Строки кода, похожие на промпт. Докстроки — документация, не промпты."""
    tree = ast.parse(source)
    docstrings = {
        id(statement.value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        for statement in node.body
        if isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and PROMPT_MARKERS.search(node.value)
    ]


def test_detector_catches_every_real_prompt_pasted_into_code() -> None:
    prompts = load_prompts()

    for key in prompts.versions():
        if key in {"set", "digest"}:
            continue
        pasted = f"SYSTEM = {prompts.get(key).text!r}\n"
        assert prompt_like_strings(pasted), f"детектор не узнал промпт {key} в коде — расширьте PROMPT_MARKERS"


def test_no_prompt_lives_in_the_code() -> None:
    found = [
        f"{path.relative_to(ROOT)}: {text[:80]!r}"
        for folder in ("src", "ui", "scripts")
        for path in sorted((ROOT / folder).rglob("*.py"))
        for text in prompt_like_strings(path.read_text(encoding="utf-8"))
    ]

    assert not found, "промпт в коде (ТЗ п. 2.4, BA-10):\n" + "\n".join(found)
