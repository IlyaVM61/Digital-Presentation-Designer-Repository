"""Проверка доступа к API-провайдеру моделей.

Запускается вручную, в пайплайн не входит:

    D:\venvs\\dpd\\Scripts\\python scripts/check_provider.py

Ключ читается из переменной окружения `LLM_API_KEY` или из файла `.env`
рядом с проектом. В репозиторий `.env` не попадает.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen3-32b")
VLM_MODEL = os.environ.get("VLM_MODEL", "qwen/qwen3-vl-32b-instruct")


def read_key() -> str | None:
    key = os.environ.get("LLM_API_KEY")
    if key:
        return key
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not env_file.is_file():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "LLM_API_KEY":
            return value.strip().strip('"').strip("'")
    return None


def ask(key: str, model: str) -> tuple[bool, str]:
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "Ответь одним словом: работает"}],
            "max_tokens": 16,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read())
        message = body["choices"][0]["message"]
        # Рассуждающие модели (Qwen3 и другие) кладут ответ в `reasoning`, а
        # `content` оставляют пустым, если лимит токенов не дал завершить
        # размышление. Пустой `content` — не отказ провайдера; клиент
        # модели пайплайна (`dpd.llm.client`, T-47) читает оба поля так же.
        text = (message.get("content") or message.get("reasoning") or "").strip()
        return True, text[:60] or "пустой ответ (модель рассуждающая)"
    except urllib.error.HTTPError as error:
        return False, f"HTTP {error.code}: {error.read().decode('utf-8', 'replace')[:200]}"
    except Exception as error:  # noqa: BLE001 — сообщаем любую причину отказа
        return False, str(error)


def main() -> int:
    key = read_key()
    if not key:
        print("Ключ не найден. Задайте LLM_API_KEY в .env или в переменных окружения.")
        return 1

    print(f"провайдер: {BASE_URL}")
    failures = 0
    for role, model in (("LLM", LLM_MODEL), ("VLM", VLM_MODEL)):
        ok, message = ask(key, model)
        print(f"  {role} {model}: {'ОК — ' + message if ok else 'ОТКАЗ — ' + message}")
        failures += 0 if ok else 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
