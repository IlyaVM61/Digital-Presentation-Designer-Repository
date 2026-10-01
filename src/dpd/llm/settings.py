"""Настройки клиента модели: `configs/models.yaml` плюс переменные окружения.

Конфиг задаёт для каждой роли — `llm` и `vlm` — модель, эндпоинт и
параметры запроса. Переменные окружения `{РОЛЬ}_BASE_URL`, `{РОЛЬ}_MODEL` и
`{РОЛЬ}_API_KEY` сильнее конфига: так переход на инференс организаторов
делается без правки файлов. Ключ у провайдера обычно один на обе роли,
поэтому VLM без собственного ключа берёт `LLM_API_KEY`.

Окружение процесса сильнее `.env`: значение, заданное при запуске, не должно
молча подменяться файлом.

Параметры, которые понимает только конкретный провайдер, — отключение
рассуждения, маршрутизация — лежат в `extra` и добавляются в тело запроса
как есть. Код о них не знает ничего.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = ROOT / "configs" / "models.yaml"
DEFAULT_ENV_FILE = ROOT / ".env"


@dataclass(frozen=True)
class ModelSettings:
    """Всё, что клиенту нужно знать о модели одной роли."""

    base_url: str
    api_key: str | None
    model: str
    temperature: float
    seed: int | None
    max_tokens: int
    timeout_sec: float
    max_attempts: int
    retry_delay_sec: float
    extra: dict[str, Any] = field(default_factory=dict)


def load_settings(
    role: str = "llm",
    *,
    config: Path | None = None,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = DEFAULT_ENV_FILE,
) -> ModelSettings:
    """Настройки роли `llm` или `vlm` из конфига с поправкой на окружение."""
    raw = yaml.safe_load((config or DEFAULT_CONFIG).read_text(encoding="utf-8")) or {}
    roles = raw.get("roles") or {}
    if role not in roles:
        known = ", ".join(sorted(roles)) or "нет ни одной"
        raise ValueError(f"Роль модели «{role}» не описана в configs/models.yaml; описаны: {known}")
    values = roles[role]

    env = {**read_env_file(env_file), **(os.environ if environ is None else environ)}
    prefix = role.upper()

    return ModelSettings(
        base_url=env.get(f"{prefix}_BASE_URL") or values["base_url"],
        api_key=env.get(f"{prefix}_API_KEY") or env.get("LLM_API_KEY") or None,
        model=env.get(f"{prefix}_MODEL") or values["model"],
        temperature=float(values["temperature"]),
        seed=values.get("seed"),
        max_tokens=int(values["max_tokens"]),
        timeout_sec=float(values["timeout_sec"]),
        max_attempts=int(values["max_attempts"]),
        retry_delay_sec=float(values["retry_delay_sec"]),
        extra=dict(values.get("extra") or {}),
    )


def read_env_file(path: Path | None) -> dict[str, str]:
    """Пары `ИМЯ=значение` из `.env`; комментарии и кавычки отбрасываются."""
    if path is None or not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values
