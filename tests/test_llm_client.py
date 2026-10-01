"""T-47: клиент модели с проверкой ответа по JSON Schema.

Невалидный ответ не уходит дальше по пайплайну: клиент повторяет запрос,
показывая модели её ответ и ошибку проверки, а после исчерпания попыток
поднимает ошибку с названием этапа (EF-8) — без частичного результата.

Провайдер подменён `httpx.MockTransport`: тесты не ходят в сеть и не тратят
бюджет, а ответы модели задаются точно, включая ошибочные.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
from pydantic import Field

from dpd.llm import ModelClient, ModelError, ModelSettings, load_settings
from dpd.models.common import Contract

REPAIR = "Ответ не прошёл проверку: {error}"


class Headline(Contract):
    title: str
    item_count: int = Field(ge=1)


def settings(**overrides: object) -> ModelSettings:
    values: dict[str, object] = {
        "base_url": "https://provider.test/v1",
        "api_key": "test-key",
        "model": "test/model",
        "temperature": 0.0,
        "seed": 42,
        "max_tokens": 500,
        "timeout_sec": 5.0,
        "max_attempts": 3,
        "retry_delay_sec": 0.0,
        "extra": {},
    }
    values.update(overrides)
    return ModelSettings(**values)  # type: ignore[arg-type]


def answer(content: str | None, reasoning: str | None = None, finish: str = "stop") -> httpx.Response:
    message = {"role": "assistant", "content": content, "reasoning": reasoning}
    return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": finish}]})


class Provider:
    """Поддельный провайдер: отдаёт ответы по очереди и запоминает запросы."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def body(self, index: int) -> dict:
        return json.loads(self.requests[index].content)


def client(provider: Provider, **overrides: object) -> ModelClient:
    return ModelClient(
        settings(**overrides), repair_prompt=REPAIR, transport=httpx.MockTransport(provider)
    )


def ask(model_client: ModelClient) -> Headline:
    return model_client.complete("системный", "запрос", Headline, stage="Структура колоды")


VALID = '{"title": "Выручка выросла", "itemCount": 3}'


# --- обычный ответ ---------------------------------------------------------


def test_valid_answer_becomes_a_contract_object() -> None:
    provider = Provider(answer(VALID))

    result = ask(client(provider))

    assert result == Headline(title="Выручка выросла", item_count=3)
    assert len(provider.requests) == 1


def test_request_carries_prompts_schema_and_sampling_settings() -> None:
    provider = Provider(answer(VALID))

    ask(client(provider))

    request = provider.requests[0]
    body = provider.body(0)
    assert str(request.url) == "https://provider.test/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert body["model"] == "test/model"
    assert body["messages"] == [
        {"role": "system", "content": "системный"},
        {"role": "user", "content": "запрос"},
    ]
    assert body["temperature"] == 0.0
    assert body["seed"] == 42
    assert body["max_tokens"] == 500
    schema = body["response_format"]["json_schema"]["schema"]
    assert body["response_format"]["type"] == "json_schema"
    assert schema == Headline.model_json_schema()
    # Схема описывает ответ в camelCase — так же, как его проверяет контракт.
    assert "itemCount" in schema["properties"]


def test_provider_specific_parameters_come_from_settings_not_code() -> None:
    """Отключение рассуждения и маршрутизация — параметры провайдера.

    Клиент не знает о них ничего: они приходят из `configs/models.yaml` и
    добавляются в тело запроса как есть. Иначе переход на инференс
    организаторов потребовал бы правки кода.
    """
    provider = Provider(answer(VALID))
    extra = {"reasoning": {"enabled": False}, "provider": {"require_parameters": True}}

    ask(client(provider, extra=extra))

    body = provider.body(0)
    assert body["reasoning"] == {"enabled": False}
    assert body["provider"] == {"require_parameters": True}


def test_request_without_key_sends_no_authorization() -> None:
    """Локальный инференс ключа не требует — заголовок не должен быть пустым."""
    provider = Provider(answer(VALID))

    ask(client(provider, api_key=None))

    assert "Authorization" not in provider.requests[0].headers


# --- извлечение ответа -----------------------------------------------------


def test_reasoning_model_answer_is_read_from_reasoning_when_content_is_empty() -> None:
    """Qwen3 кладёт текст в `reasoning`, оставляя `content` пустым (MODELS.md)."""
    provider = Provider(answer("", reasoning=VALID))

    assert ask(client(provider)).title == "Выручка выросла"


@pytest.mark.parametrize(
    "content",
    [
        f"```json\n{VALID}\n```",
        f"<think>сначала подумаю</think>\n{VALID}",
        f"Вот ответ: {VALID} Готово.",
    ],
    ids=["code-fence", "think-block", "surrounding-text"],
)
def test_json_is_found_inside_common_wrappers(content: str) -> None:
    provider = Provider(answer(content))

    assert ask(client(provider)).item_count == 3


# --- повтор с указанием ошибки ---------------------------------------------


def test_malformed_json_triggers_retry_with_the_error_shown_to_the_model() -> None:
    provider = Provider(answer('{"title": "Выручка'), answer(VALID))

    result = ask(client(provider))

    assert result.title == "Выручка выросла"
    assert len(provider.requests) == 2
    retry = provider.body(1)["messages"]
    assert retry[:2] == provider.body(0)["messages"]
    assert retry[2] == {"role": "assistant", "content": '{"title": "Выручка'}
    assert retry[3]["role"] == "user"
    assert retry[3]["content"].startswith("Ответ не прошёл проверку: ")


def test_schema_violation_names_the_offending_field_in_the_retry() -> None:
    provider = Provider(answer('{"title": "Выручка выросла", "itemCount": 0}'), answer(VALID))

    ask(client(provider))

    repair = provider.body(1)["messages"][3]["content"]
    assert "itemCount" in repair
    # Ссылки на документацию pydantic модели ни к чему — только суть ошибки.
    assert "errors.pydantic.dev" not in repair


def test_retry_shows_only_the_latest_bad_answer() -> None:
    """Контекст повтора не растёт с каждой попыткой: только последний ответ."""
    provider = Provider(answer("первый"), answer("второй"), answer(VALID))

    ask(client(provider))

    third = provider.body(2)["messages"]
    assert len(third) == 4
    assert third[2]["content"] == "второй"


def test_repair_prompt_braces_are_left_alone() -> None:
    """Промпт может содержать JSON-пример: подставляется только `{error}`."""
    provider = Provider(answer("мусор"), answer(VALID))
    model_client = ModelClient(
        settings(),
        repair_prompt='Пример: {"title": "…"}. Ошибка: {error}',
        transport=httpx.MockTransport(provider),
    )

    ask(model_client)

    repair = provider.body(1)["messages"][3]["content"]
    assert repair.startswith('Пример: {"title": "…"}. Ошибка: ')


# --- исчерпание попыток ----------------------------------------------------


def test_exhausted_attempts_raise_an_error_naming_the_stage() -> None:
    provider = Provider(answer("мусор"), answer("мусор"), answer("мусор"))

    with pytest.raises(ModelError) as caught:
        ask(client(provider))

    error = caught.value
    assert len(provider.requests) == 3
    assert error.stage == "Структура колоды"
    assert error.attempts == 3
    message = str(error)
    assert "Структура колоды" in message
    assert "test/model" in message
    assert "3" in message
    assert "Headline" in message


def test_exhausted_attempts_hand_over_the_last_answer() -> None:
    """T-61: ответ, не прошедший проверку, не пропадает вместе с ошибкой —
    слой генерации решает, можно ли сохранить из него слайд. Сбой провайдера
    на последней попытке не стирает ответ предыдущей: так оборвался третий
    запрос в диагностике T-59."""
    last = '{"title": "Выручка выросла", "itemCount": 0}'
    provider = Provider(answer("мусор"), answer(last), httpx.Response(503, text="перегружен"))

    with pytest.raises(ModelError) as caught:
        ask(client(provider))

    assert caught.value.answer == last


def test_error_without_any_answer_hands_over_nothing() -> None:
    timeout = httpx.ReadTimeout("слишком долго")
    provider = Provider(timeout, timeout, timeout)

    with pytest.raises(ModelError) as caught:
        ask(client(provider))

    assert caught.value.answer is None


def test_truncated_answer_is_reported_as_truncation() -> None:
    """Обрезанный по лимиту JSON — не каприз модели, а нехватка `max_tokens`."""
    cut = answer('{"title": "Выруч', finish="length")
    provider = Provider(cut, cut, cut)

    with pytest.raises(ModelError) as caught:
        ask(client(provider))

    assert "max_tokens" in str(caught.value)


def test_attempt_limit_comes_from_settings() -> None:
    provider = Provider(answer("мусор"), answer("мусор"))

    with pytest.raises(ModelError):
        ask(client(provider, max_attempts=2))

    assert len(provider.requests) == 2


# --- недоступность провайдера ----------------------------------------------


def test_server_error_is_retried() -> None:
    provider = Provider(httpx.Response(503, text="перегружен"), answer(VALID))

    assert ask(client(provider)).title == "Выручка выросла"
    # Повтор после сбоя провайдера — тот же запрос: чинить в ответе нечего.
    assert provider.body(1)["messages"] == provider.body(0)["messages"]


def test_timeout_is_retried_and_then_reported() -> None:
    timeout = httpx.ReadTimeout("слишком долго")
    provider = Provider(timeout, timeout, timeout)

    with pytest.raises(ModelError) as caught:
        ask(client(provider))

    assert len(provider.requests) == 3
    assert "Структура колоды" in str(caught.value)


def test_rejected_request_fails_at_once_with_a_hint() -> None:
    """401 не лечится повтором: ключ от него не станет верным."""
    provider = Provider(httpx.Response(401, json={"error": {"message": "No auth credentials"}}))

    with pytest.raises(ModelError) as caught:
        ask(client(provider))

    assert len(provider.requests) == 1
    assert "401" in str(caught.value)
    assert "LLM_API_KEY" in str(caught.value)


def test_rate_limit_waits_before_retrying() -> None:
    pauses: list[float] = []
    provider = Provider(httpx.Response(429, text="slow down"), answer(VALID))
    model_client = ModelClient(
        settings(retry_delay_sec=2.0),
        repair_prompt=REPAIR,
        transport=httpx.MockTransport(provider),
        sleep=pauses.append,
    )

    ask(model_client)

    assert pauses == [2.0]


def test_answer_without_choices_is_retried() -> None:
    """OpenRouter порой отвечает 200 с ошибкой вместо `choices`."""
    broken = httpx.Response(200, json={"error": {"message": "upstream failed"}})
    provider = Provider(broken, answer(VALID))

    assert ask(client(provider)).item_count == 3

# --- изображение в запросе (T-52) ------------------------------------------


def test_image_goes_into_the_user_message_next_to_the_text() -> None:
    """Аудит визуала показывает модели слайд: изображение идёт в сообщении
    пользователя частью `image_url`, как принято в OpenAI-совместимом API."""
    provider = Provider(answer(VALID))

    client(provider).complete("системный", "запрос", Headline, stage="Вид слайдов", images=[b"\x89PNG-1"])

    system, user = provider.body(0)["messages"]
    assert system == {"role": "system", "content": "системный"}
    assert user["role"] == "user"
    assert user["content"] == [
        {"type": "text", "text": "запрос"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b"\x89PNG-1").decode()}},
    ]


def test_retry_shows_the_image_again() -> None:
    """Повтор без изображения просил бы модель исправить ответ о слайде, которого она не видит."""
    provider = Provider(answer('{"title": "Выручка'), answer(VALID))

    client(provider).complete("системный", "запрос", Headline, stage="Вид слайдов", images=[b"png"])

    assert provider.body(1)["messages"][:2] == provider.body(0)["messages"]


# --- настройки -------------------------------------------------------------


def write_config(tmp_path: Path) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(
        """
version: 1
roles:
  llm:
    base_url: https://from-config.test/v1
    model: config/llm
    temperature: 0
    seed: 7
    max_tokens: 4000
    timeout_sec: 90
    max_attempts: 3
    retry_delay_sec: 1
    extra:
      reasoning: {enabled: false}
  vlm:
    base_url: https://from-config.test/v1
    model: config/vlm
    temperature: 0
    seed: 7
    max_tokens: 1000
    timeout_sec: 60
    max_attempts: 2
    retry_delay_sec: 1
""",
        encoding="utf-8",
    )
    return path


def test_settings_come_from_config(tmp_path: Path) -> None:
    loaded = load_settings("llm", config=write_config(tmp_path), environ={}, env_file=None)

    assert loaded.base_url == "https://from-config.test/v1"
    assert loaded.model == "config/llm"
    assert loaded.seed == 7
    assert loaded.max_tokens == 4000
    assert loaded.extra == {"reasoning": {"enabled": False}}
    assert loaded.api_key is None


def test_environment_overrides_endpoint_model_and_key(tmp_path: Path) -> None:
    environ = {
        "VLM_BASE_URL": "http://localhost:8001/v1",
        "VLM_MODEL": "local/vlm",
        "LLM_API_KEY": "secret",
    }

    loaded = load_settings("vlm", config=write_config(tmp_path), environ=environ, env_file=None)

    assert loaded.base_url == "http://localhost:8001/v1"
    assert loaded.model == "local/vlm"
    # Ключ у провайдера один на обе роли; отдельный VLM_API_KEY не обязателен.
    assert loaded.api_key == "secret"
    assert loaded.max_attempts == 2


def test_key_is_read_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('# секреты\nLLM_API_KEY="from-file"\n', encoding="utf-8")

    loaded = load_settings("llm", config=write_config(tmp_path), environ={}, env_file=env_file)

    assert loaded.api_key == "from-file"


def test_process_environment_wins_over_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_MODEL=from-file\n", encoding="utf-8")

    loaded = load_settings(
        "llm", config=write_config(tmp_path), environ={"LLM_MODEL": "from-env"}, env_file=env_file
    )

    assert loaded.model == "from-env"


def test_unknown_role_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="t2i"):
        load_settings("t2i", config=write_config(tmp_path), environ={}, env_file=None)


def test_project_config_defines_both_roles() -> None:
    """`configs/models.yaml` проекта читается без правок и знает обе роли."""
    for role in ("llm", "vlm"):
        loaded = load_settings(role, environ={}, env_file=None)
        assert loaded.model
        assert loaded.base_url.startswith("http")
        assert loaded.max_attempts >= 2
