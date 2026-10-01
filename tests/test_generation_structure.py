"""T-49: генерация структуры колоды по брифу.

Модель получает бриф и фактуру контент-пакета и одним запросом возвращает
состав колоды: роль, заголовок и ключевое сообщение каждого слайда. Объём —
10–15 слайдов (FR-13) или заданный пользователем; он записан в схему ответа,
поэтому провайдер держит его при декодировании, а невалидный ответ всё равно
повторяется клиентом T-47.

Провайдер подменён `httpx.MockTransport`: тест проверяет код, а не модель.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import httpx
import pytest

from dpd.generation import (
    DEFAULT_SLIDE_RANGE,
    STRUCTURE_STAGE,
    ContentPack,
    generate_structure,
    load_content_pack,
)
from dpd.layout.selector import ROLE_TO_FAMILY, requested_family
from dpd.llm import ModelClient, ModelError, ModelSettings, load_prompts
from dpd.models.structure import SlideRole

PACK = Path(__file__).resolve().parents[1] / "assets" / "content-pack"
BRIEF = "# Бриф\n\nПросим решение о масштабировании пилота."
CONTENT = "# Фактура\n\n## Результаты пилота\n\nУдержание выросло на 11 п. п."


def settings() -> ModelSettings:
    return ModelSettings(
        base_url="https://provider.test/v1",
        api_key="test-key",
        model="test/model",
        temperature=0.0,
        seed=42,
        max_tokens=500,
        timeout_sec=5.0,
        max_attempts=3,
        retry_delay_sec=0.0,
        extra={},
    )


def outline(count: int = 12, *, first_role: str = "title", key_message: str = "Что унести из слайда") -> str:
    roles = [first_role, "agenda", *["data"] * max(count - 3, 0), "closing"][:count]
    return json.dumps(
        {
            "meta": {"purpose": "initiative", "audience": "руководители подразделений", "language": "ru"},
            "slides": [
                {"role": role, "headline": f"Заголовок {index}", "keyMessage": key_message}
                for index, role in enumerate(roles, start=1)
            ],
        },
        ensure_ascii=False,
    )


class Provider:
    """Поддельный провайдер: отдаёт ответы по очереди и запоминает запросы."""

    def __init__(self, *contents: str) -> None:
        self.contents = list(contents)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        message = {"role": "assistant", "content": self.contents.pop(0)}
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}]})


def generate(provider: Provider, **kwargs: object):
    prompts = load_prompts()
    client = ModelClient(
        settings(),
        repair_prompt=prompts.get("skills/response-repair").text,
        transport=httpx.MockTransport(provider),
    )
    pack = ContentPack(brief=BRIEF, content=CONTENT)
    return generate_structure(pack, client, prompts, **kwargs)  # type: ignore[arg-type]


# --- Результат --------------------------------------------------------------


def test_every_slide_gets_a_role_headline_and_key_message() -> None:
    structure = generate(Provider(outline(12)))

    assert len(structure.slides) == 12
    assert [slide.id for slide in structure.slides] == [f"s{n}" for n in range(1, 13)]
    assert structure.slides[0].role == "title"
    assert structure.slides[-1].role == "closing"
    assert all(slide.headline and slide.key_message for slide in structure.slides)
    assert structure.meta.purpose == "initiative"
    assert structure.meta.audience == "руководители подразделений"
    assert structure.meta.language == "ru"


def test_structure_carries_no_form_and_no_invented_content() -> None:
    """Тело, визуализация и ссылки на источник — работа T-50, не структуры."""
    structure = generate(Provider(outline(10)))

    for slide in structure.slides:
        assert slide.body is None
        assert slide.visualization is None
        assert slide.source_refs == []


# --- Запрос -------------------------------------------------------------------


def test_request_carries_the_prompt_from_file_and_the_content_pack() -> None:
    provider = Provider(outline(12))
    generate(provider)

    system, user = provider.requests[0]["messages"]
    expected = load_prompts().get("skills/outline-generator").render(min_slides="10", max_slides="15")
    assert system == {"role": "system", "content": expected}
    assert "{min_slides}" not in system["content"]
    assert BRIEF in user["content"] and CONTENT in user["content"]


def test_slide_range_and_roles_are_written_into_the_response_schema() -> None:
    provider = Provider(outline(12))
    generate(provider)

    schema = provider.requests[0]["response_format"]["json_schema"]["schema"]
    slides = schema["properties"]["slides"]
    assert (slides["minItems"], slides["maxItems"]) == DEFAULT_SLIDE_RANGE == (10, 15)
    role = schema["$defs"]["OutlineSlide"]["properties"]["role"]
    assert set(role["enum"]) == set(get_args(SlideRole))


def test_count_set_by_the_user_overrides_the_default_range() -> None:
    provider = Provider(outline(6))
    structure = generate(provider, slide_range=(6, 6))

    slides = provider.requests[0]["response_format"]["json_schema"]["schema"]["properties"]["slides"]
    assert (slides["minItems"], slides["maxItems"]) == (6, 6)
    assert len(structure.slides) == 6
    expected = load_prompts().get("skills/outline-generator").render(min_slides="6", max_slides="6")
    assert provider.requests[0]["messages"][0]["content"] == expected


@pytest.mark.parametrize("bad", [(0, 5), (12, 10)])
def test_impossible_range_is_refused_before_the_request(bad: tuple[int, int]) -> None:
    provider = Provider()
    with pytest.raises(ValueError, match="слайд"):
        generate(provider, slide_range=bad)
    assert provider.requests == []


# --- Невалидный ответ -------------------------------------------------------


@pytest.mark.parametrize(
    "wrong",
    [
        pytest.param(outline(4), id="мало-слайдов"),
        pytest.param(outline(16), id="много-слайдов"),
        pytest.param(outline(12, first_role="data"), id="первый-не-титульный"),
        pytest.param(outline(12, key_message=""), id="без-ключевого-сообщения"),
        pytest.param(outline(12, first_role="intro"), id="роль-вне-словаря"),
    ],
)
def test_wrong_outline_is_repaired_by_a_second_request(wrong: str) -> None:
    provider = Provider(wrong, outline(12))
    structure = generate(provider)

    assert len(provider.requests) == 2
    assert len(structure.slides) == 12
    assert provider.requests[1]["messages"][2] == {"role": "assistant", "content": wrong}


def test_outline_that_stays_wrong_stops_with_the_stage_named() -> None:
    provider = Provider(outline(4), outline(4), outline(4))

    with pytest.raises(ModelError) as error:
        generate(provider)

    assert error.value.stage == STRUCTURE_STAGE == "Структура колоды"
    assert len(provider.requests) == 3


# --- Словарь ролей ------------------------------------------------------------


def test_roles_are_exactly_the_ones_layout_knows() -> None:
    """Роль вне таблицы вёрстка молча считает контентной — генератор не должен её выдавать."""
    assert set(get_args(SlideRole)) == set(ROLE_TO_FAMILY)


def test_prompt_explains_every_role_to_the_model() -> None:
    text = load_prompts().get("skills/outline-generator").text
    missing = [role for role in get_args(SlideRole) if f"`{role}`" not in text]
    assert missing == []


def test_generated_title_slide_lands_on_a_title_layout() -> None:
    structure = generate(Provider(outline(12)))

    assert requested_family(structure.slides[0]) == "title"


# --- Контент-пакет ------------------------------------------------------------


def test_content_pack_is_read_from_its_directory() -> None:
    pack = load_content_pack(PACK)

    assert pack.brief.startswith("# Бриф")
    assert "## Результаты пилота" in pack.content


def test_content_pack_without_a_brief_is_refused(tmp_path: Path) -> None:
    (tmp_path / "content.md").write_text(CONTENT, encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="brief.md"):
        load_content_pack(tmp_path)
