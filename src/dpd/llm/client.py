"""Клиент модели: запрос по OpenAI-совместимому API и проверка ответа по схеме.

Клиент принимает системный промпт, запрос и контракт ответа — модель
`pydantic`. По контракту строится JSON Schema для `response_format`, им же
проверяется ответ: схема запроса и проверка не могут разойтись.

**Невалидный ответ дальше не уходит** (EF-8). Клиент повторяет запрос,
показывая модели её последний ответ и ошибку проверки; после исчерпания
попыток поднимает `ModelError` с названием этапа. Частичного результата нет.

Сбой провайдера — таймаут, HTTP 429 и 5xx, ответ без `choices` — тоже
повторяется, но с прежними сообщениями: чинить в ответе нечего. Отказ
4xx повтором не лечится — неверный ключ от него верным не станет, — и
ошибка поднимается сразу.

Текст ответа берётся из `content`, а если тот пуст — из `reasoning`:
Qwen3 кладёт текст туда (MODELS.md). Вокруг JSON модели любят оставлять
`<think>`, ограды кода и вежливые фразы — всё это отбрасывается до проверки.

Текст просьбы исправить ответ — промпт, а промптам в коде не место (ТЗ,
п. 2.4): клиент получает его снаружи, из `prompts/`.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from dpd.llm.settings import ModelSettings

T = TypeVar("T", bound=BaseModel)

THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
ERROR_PLACEHOLDER = "{error}"
MAX_REPORTED_ERRORS = 10


class ModelError(RuntimeError):
    """Модель не дала годного ответа; сообщение называет этап (EF-8)."""

    def __init__(self, stage: str, message: str, attempts: int) -> None:
        super().__init__(f"Этап «{stage}»: {message}")
        self.stage = stage
        self.attempts = attempts


class ProviderFailure(Exception):
    """Сбой на стороне провайдера, который имеет смысл повторить."""


class ModelClient:
    """Запросы к модели одной роли с проверкой ответа по контракту."""

    def __init__(
        self,
        settings: ModelSettings,
        *,
        repair_prompt: str,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.repair_prompt = repair_prompt
        self.transport = transport
        self.sleep = sleep

    def complete(self, system: str, user: str, schema: type[T], *, stage: str) -> T:
        """Ответ модели как объект контракта `schema` — или `ModelError`."""
        base = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        messages = base
        attempts = self.settings.max_attempts
        reason = ""

        with httpx.Client(timeout=self.settings.timeout_sec, transport=self.transport) as http:
            for attempt in range(1, attempts + 1):
                try:
                    text, finish = self._send(http, messages, schema, stage, attempt)
                except ProviderFailure as failure:
                    reason = str(failure)
                    if attempt < attempts:
                        self.sleep(self.settings.retry_delay_sec)
                    continue

                try:
                    return schema.model_validate_json(extract_json(text))
                except ValidationError as error:
                    reason = describe(error)
                    if finish == "length":
                        reason = (
                            f"ответ обрезан по лимиту max_tokens={self.settings.max_tokens}; {reason}"
                        )
                    repair = self.repair_prompt.replace(ERROR_PLACEHOLDER, reason)
                    messages = [
                        *base,
                        {"role": "assistant", "content": text},
                        {"role": "user", "content": repair},
                    ]

        raise ModelError(
            stage,
            f"модель {self.settings.model} не вернула ответ по схеме {schema.__name__} "
            f"(попыток: {attempts}). Последняя причина: {reason}",
            attempts,
        )

    def _send(
        self,
        http: httpx.Client,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
        stage: str,
        attempt: int,
    ) -> tuple[str, str | None]:
        """Один запрос: текст ответа и `finish_reason`, или `ProviderFailure`."""
        settings = self.settings
        body = {
            "model": settings.model,
            "messages": messages,
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            },
        }
        if settings.seed is not None:
            body["seed"] = settings.seed
        body.update(settings.extra)

        headers = {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
        url = f"{settings.base_url.rstrip('/')}/chat/completions"

        try:
            response = http.post(url, json=body, headers=headers)
        except httpx.TimeoutException:
            raise ProviderFailure(f"провайдер не ответил за {settings.timeout_sec:g} с") from None
        except httpx.TransportError as error:
            raise ProviderFailure(f"провайдер недоступен: {error}") from None

        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderFailure(f"провайдер вернул HTTP {response.status_code}: {snippet(response.text)}")
        if response.status_code >= 400:
            raise ModelError(
                stage,
                f"провайдер отклонил запрос к модели {settings.model}, HTTP {response.status_code}: "
                f"{snippet(response.text)}. Проверьте ключ, адрес и модель в .env "
                f"(LLM_API_KEY, LLM_BASE_URL, LLM_MODEL)",
                attempt,
            )

        try:
            choice = response.json()["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderFailure(f"ответ провайдера без choices: {snippet(response.text)}") from None

        text = message.get("content") or message.get("reasoning") or ""
        return text, choice.get("finish_reason")


def extract_json(text: str) -> str:
    """JSON-объект из ответа модели без `<think>`, оград кода и пояснений."""
    text = THINK_BLOCK.sub("", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        return text.strip()
    return text[start : end + 1]


def describe(error: ValidationError) -> str:
    """Суть ошибок проверки — для модели и для человека, без ссылок pydantic."""
    parts = []
    for item in error.errors(include_url=False)[:MAX_REPORTED_ERRORS]:
        where = ".".join(str(part) for part in item["loc"]) or "ответ целиком"
        parts.append(f"{where}: {item['msg']}")
    return "; ".join(parts)


def snippet(text: str, limit: int = 200) -> str:
    return " ".join(text.split())[:limit]


__all__ = ["ModelClient", "ModelError", "extract_json"]
