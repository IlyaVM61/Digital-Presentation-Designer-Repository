"""Загрузка и версионирование промптов из `prompts/`.

Промптам в коде не место — прямое требование ТЗ (п. 2.4) и приёмки BA-10.
Каждый файл в `prompts/` начинается заголовком YAML между строками `---`:
обязательное поле `version` и необязательное `placeholders` — подстановки
вида `{name}` с описанием. Всё после заголовка и есть промпт, ровно в том
виде, в каком он уйдёт модели.

**Версия набора ведётся в `prompts/CHANGELOG.md`** — первый заголовок `## `
журнала. В `AuditReport` попадают она, версия каждого промпта и хеш
содержимого: правку текста без подъёма версии человек сделает обязательно, и
одна версия её бы скрыла.

**Пустой файл — промпт, который ещё не написан.** В `prompts/` лежат
заготовки для задач, которые впереди. Загрузка их пропускает, а обращение к
такому промпту — громкая ошибка с именем файла: пустой системный промпт,
молча ушедший модели, дал бы ответ ни о чём.

**Битый файл останавливает загрузку целиком.** Промпты читаются на старте
прогона, и ошибка в заголовке видна до разбора шаблона, а не посреди
генерации, когда на неё уже потрачены минуты и деньги.

Ключ промпта — путь от `prompts/` без расширения: `skills/response-repair`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROMPTS = ROOT / "prompts"
CHANGELOG = "CHANGELOG.md"
NOT_PROMPTS = frozenset({CHANGELOG, "README.md"})
"""Файлы корня `prompts/`, которые описывают набор, а не входят в него."""

CHANGELOG_ENTRY = re.compile(r"^## +([^\s—–-]+)", re.MULTILINE)


class PromptError(ValueError):
    """Промпт не читается или запрошен неверно; сообщение называет файл."""


@dataclass(frozen=True)
class Prompt:
    """Текст промпта с версией и объявленными подстановками."""

    key: str
    version: str
    text: str
    placeholders: tuple[str, ...] = ()

    def render(self, **values: str) -> str:
        """Подставить значения; фигурные скобки вне объявленных — текст.

        Подставляется только объявленное в заголовке: пример JSON внутри
        промпта не должен ломаться о `str.format`.
        """
        expected, given = set(self.placeholders), set(values)
        if expected != given:
            missing = ", ".join(sorted(expected - given)) or "—"
            extra = ", ".join(sorted(given - expected)) or "—"
            raise PromptError(f"Промпт {self.key}: не хватает подстановок {missing}, лишние {extra}")
        text = self.text
        for name, value in values.items():
            text = text.replace(f"{{{name}}}", value)
        return text


@dataclass(frozen=True)
class PromptSet:
    """Промпты одного прогона и версия набора."""

    version: str
    prompts: Mapping[str, Prompt]
    unwritten: frozenset[str] = frozenset()

    def get(self, key: str) -> Prompt:
        if key in self.prompts:
            return self.prompts[key]
        if key in self.unwritten:
            raise PromptError(f"Промпт prompts/{key}.md ещё не написан: файл пуст")
        raise PromptError(f"Промпта prompts/{key}.md нет")

    def versions(self) -> dict[str, str]:
        """Сведения для `AuditReport.prompt_versions`."""
        digest = hashlib.sha256()
        for key in sorted(self.prompts):
            prompt = self.prompts[key]
            digest.update(f"{key}\n{prompt.version}\n{prompt.text}\n".encode())
        return {
            "set": self.version,
            "digest": digest.hexdigest()[:12],
            **{key: self.prompts[key].version for key in sorted(self.prompts)},
        }


def load_prompts(root: Path | None = None) -> PromptSet:
    """Прочитать все промпты каталога и версию набора из журнала."""
    root = root or DEFAULT_PROMPTS
    prompts: dict[str, Prompt] = {}
    unwritten: set[str] = set()
    for path in sorted(root.rglob("*.md")):
        if path.parent == root and path.name in NOT_PROMPTS:
            continue
        key = path.relative_to(root).with_suffix("").as_posix()
        raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        if not raw.strip():
            unwritten.add(key)
            continue
        prompts[key] = parse_prompt(key, raw, path)
    return PromptSet(version=set_version(root), prompts=prompts, unwritten=frozenset(unwritten))


def parse_prompt(key: str, raw: str, path: Path) -> Prompt:
    def fail(reason: str) -> PromptError:
        return PromptError(f"{path.name} ({path}): {reason}")

    if not raw.startswith("---\n"):
        raise fail("нет заголовка YAML между строками `---`")
    header, separator, body = raw[4:].partition("\n---\n")
    if not separator:
        raise fail("заголовок YAML не закрыт строкой `---`")
    try:
        meta = yaml.safe_load(header) or {}
    except yaml.YAMLError as error:
        raise fail(f"заголовок не разбирается: {error}") from error
    if not isinstance(meta, dict):
        raise fail("заголовок должен быть словарём YAML")

    version = meta.get("version")
    if isinstance(version, bool) or not isinstance(version, (int, float, str)) or not str(version).strip():
        raise fail("в заголовке нет поля `version` или оно не число и не строка")
    text = body.strip()
    if not text:
        raise fail("после заголовка нет текста промпта")

    placeholders = tuple(meta.get("placeholders") or ())
    absent = [name for name in placeholders if f"{{{name}}}" not in text]
    if absent:
        raise fail("объявленных подстановок нет в тексте: " + ", ".join(f"{{{name}}}" for name in absent))
    return Prompt(key=key, version=str(version).strip(), text=text, placeholders=placeholders)


def set_version(root: Path) -> str:
    path = root / CHANGELOG
    if not path.is_file():
        raise PromptError(f"Нет журнала набора промптов {CHANGELOG} в {root}: версии набора неоткуда взяться")
    entry = CHANGELOG_ENTRY.search(path.read_text(encoding="utf-8-sig"))
    if entry is None:
        raise PromptError(f"В {path} нет записи `## <версия>`: версия набора не определена")
    return entry.group(1)
