"""T-50: содержание слайдов со ссылками на источник.

По структуре колоды (T-49) модель пишет каждому слайду тело или
визуализацию и ссылки на разделы фактуры. Запрос — на слайд, потоки
параллельны: так заложено в бюджете `models-strategy.md`.

**Каждое число на слайде прослеживается до контент-пакета.** Ссылки на
источник ограничены схемой ответа — якорями разделов фактуры, а число,
которого нет в разделах, на которые слайд ссылается, не проходит проверку
контракта, и клиент T-47 повторяет запрос с перечнем ошибок.

Провайдер подменён `httpx.MockTransport`: тест проверяет код, а не модель.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest

from dpd.generation import (
    CONTENT_STAGE,
    ContentPack,
    fact_sections,
    generate_content,
    load_content_pack,
    numbers_in,
)
from dpd.generation.content import fact_numbers
from dpd.layout.selector import requested_family
from dpd.llm import ModelClient, ModelError, ModelSettings, load_prompts
from dpd.models import PresentationStructure, StructureSlide
from dpd.models.structure import StructureMeta

PACK = Path(__file__).resolve().parents[1] / "assets" / "content-pack"
BRIEF = "# Бриф\n\nПросим решение о масштабировании пилота."
CONTENT = """# Фактура

## Результаты пилота

Удержание в первый год выросло с 78% до 89%, на 11 п. п.

## Масштаб пилота

| Квартал | Активных пар |
|---|---|
| I кв. 2026 | 34 |
| II кв. 2026 | 61 |

Всего через программу прошли 1 240 человек.
"""
RESULTS = "content.md#результаты-пилота"
SCALE = "content.md#масштаб-пилота"


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


def structure(*roles: str) -> PresentationStructure:
    roles = roles or ("title", "data", "closing")
    return PresentationStructure(
        meta=StructureMeta(language="ru", purpose="initiative", audience="руководители"),
        slides=[
            StructureSlide(id=f"s{n}", role=role, headline=f"Заголовок {n}", key_message=f"Мысль {n}")
            for n, role in enumerate(roles, start=1)
        ],
    )


def answer(
    *,
    headline: str = "Заголовок-вывод",
    key_message: str = "Что унести из слайда",
    body: list[str] | None = None,
    visualization: dict | None = None,
    refs: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "headline": headline,
            "keyMessage": key_message,
            "body": {"kind": "bullets", "items": body} if body is not None else None,
            "visualization": visualization,
            "sourceRefs": refs if refs is not None else [],
        },
        ensure_ascii=False,
    )


def content_answer() -> str:
    return answer(body=["Удержание выросло с 78% до 89%"], refs=[RESULTS])


def title_answer() -> str:
    return answer(headline="Программа наставничества")


def chart(points: list[float], *, value_axis: str | None = "пар") -> dict:
    return {
        "kind": "chart",
        "table": None,
        "chart": {
            "chartType": "column",
            "categories": ["I кв. 2026", "II кв. 2026"],
            "series": [{"name": "Активных пар", "points": points}],
            "axisTitles": {"category": "квартал", "value": value_axis},
        },
    }


def table(rows: list[list[str]]) -> dict:
    return {"kind": "table", "chart": None, "table": {"headers": ["Квартал", "Пар"], "rows": rows}}


class Provider:
    """Поддельный провайдер: ответы по номеру слайда, по очереди.

    Слайды пишутся параллельно, поэтому очередь одна на слайд, а не общая:
    какой запрос придёт первым, решают потоки.
    """

    def __init__(self, answers: dict[int, list[str]]) -> None:
        self.answers = {number: list(queue) for number, queue in answers.items()}
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        number = task(body)["slide"]["number"]
        with self.lock:
            self.requests.append(body)
            content = self.answers[number].pop(0)
        message = {"role": "assistant", "content": content}
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}]})

    def for_slide(self, number: int) -> list[dict]:
        return [body for body in self.requests if task(body)["slide"]["number"] == number]


def task(body: dict) -> dict:
    """Задание из запроса — последний абзац сообщения пользователя, JSON."""
    return json.loads(body["messages"][1]["content"].rsplit("\n\n", 1)[1])


def generate(provider: Provider, deck: PresentationStructure | None = None, **kwargs: object):
    prompts = load_prompts()
    client = ModelClient(
        settings(),
        repair_prompt=prompts.get("skills/response-repair").text,
        transport=httpx.MockTransport(provider),
    )
    pack = ContentPack(brief=BRIEF, content=CONTENT)
    return generate_content(deck or structure(), pack, client, prompts, **kwargs)  # type: ignore[arg-type]


def regular(**overrides: list[str]) -> Provider:
    answers = {1: [title_answer()], 2: [content_answer()], 3: [content_answer()]}
    answers.update({int(number[1:]): queue for number, queue in overrides.items()})
    return Provider(answers)


# --- Разделы и числа фактуры ---------------------------------------------------


def test_every_section_of_the_facts_gets_an_anchor() -> None:
    sections = fact_sections(CONTENT)

    assert list(sections) == ["content.md#фактура", RESULTS, SCALE]
    assert "78%" in sections[RESULTS]
    assert "61" in sections[SCALE] and "78%" not in sections[SCALE]


def test_anchors_of_the_real_content_pack() -> None:
    sections = fact_sections(load_content_pack(PACK).content)

    assert {"content.md#масштаб-пилота", "content.md#результаты-пилота", "content.md#запрос"} <= set(sections)


def test_facts_without_headings_are_one_section() -> None:
    assert list(fact_sections("Удержание выросло на 11 п. п.")) == ["content.md"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("с 1 240 до 1 610 человек", {"1240", "1610"}),
        ("4,5 мес против 3.1 мес", {"4.5", "3.1"}),
        ("удержание 78%, NPS 62", {"78", "62"}),
        ("−1,4 мес и +11 п. п.", {"1.4", "11"}),
        ("бюджет 7,2 млн ₽ на 2027 год", {"7.2", "2027"}),
        ("каждый пятый новичок", set()),
    ],
)
def test_numbers_are_compared_regardless_of_spelling(text: str, expected: set[str]) -> None:
    assert numbers_in(text) == expected


# --- Результат -----------------------------------------------------------------


def test_every_content_slide_gets_a_body_and_its_sources() -> None:
    result = generate(regular())

    assert [slide.id for slide in result.slides] == ["s1", "s2", "s3"]
    assert [slide.role for slide in result.slides] == ["title", "data", "closing"]
    data = result.slides[1]
    assert data.body is not None and data.body.items == ["Удержание выросло с 78% до 89%"]
    assert data.source_refs == [RESULTS]
    assert data.headline == "Заголовок-вывод"
    assert data.key_message == "Что унести из слайда"
    assert result.meta == structure().meta


def test_title_slide_stays_without_body_and_keeps_its_layout() -> None:
    result = generate(regular())

    title = result.slides[0]
    assert title.body is None and title.visualization is None
    assert requested_family(title) == "title"


def test_visualization_reaches_the_structure() -> None:
    result = generate(regular(s2=[answer(visualization=chart([34, 61]), refs=[SCALE])]))

    visual = result.slides[1].visualization
    assert visual is not None and visual.chart is not None
    assert visual.chart.series[0].points == [34, 61]
    assert result.slides[1].body is None


def test_slides_keep_their_order_whatever_thread_answers_first() -> None:
    roles = ("title", *["data"] * 10, "closing")
    # Пункты без цифр: число вне фактуры не прошло бы проверку.
    marks = {n: f"Пункт {'и' * n}" for n in range(2, 13)}
    answers = {1: [title_answer()]}
    answers.update({n: [answer(body=[mark], refs=[RESULTS])] for n, mark in marks.items()})

    result = generate(Provider(answers), structure(*roles), workers=4)

    assert [slide.body.items[0] for slide in result.slides[1:]] == list(marks.values())


# --- Запрос ----------------------------------------------------------------------


def test_one_request_per_slide_with_the_prompt_from_file() -> None:
    provider = regular()
    generate(provider)

    assert len(provider.requests) == 3
    expected = load_prompts().get("skills/slide-content-generator").text
    assert all(body["messages"][0] == {"role": "system", "content": expected} for body in provider.requests)


def test_request_carries_the_brief_the_labelled_facts_and_the_slide() -> None:
    provider = regular()
    generate(provider)

    body = provider.for_slide(2)[0]
    user = body["messages"][1]["content"]
    assert BRIEF in user
    assert f"[{RESULTS}]" in user and f"[{SCALE}]" in user
    job = task(body)
    assert job["slide"] == {"number": 2, "role": "data", "headline": "Заголовок 2", "keyMessage": "Мысль 2"}
    assert [entry["headline"] for entry in job["deck"]] == ["Заголовок 1", "Заголовок 2", "Заголовок 3"]


def test_source_refs_are_limited_to_the_anchors_by_the_schema() -> None:
    provider = regular()
    generate(provider)

    schema = provider.requests[0]["response_format"]["json_schema"]["schema"]
    refs = schema["properties"]["sourceRefs"]["items"]
    assert set(refs["enum"]) == set(fact_sections(CONTENT))


# --- Число без источника и другие нарушения ------------------------------------------


@pytest.mark.parametrize(
    ("wrong", "reason"),
    [
        pytest.param(answer(body=["Удержание выросло на 20%"], refs=[RESULTS]), "20", id="число-не-из-фактуры"),
        pytest.param(answer(body=["Удержание 89%"], refs=[SCALE]), RESULTS, id="ссылка-не-на-тот-раздел"),
        pytest.param(answer(headline="Пар стало 61", body=["Пункт"], refs=[RESULTS]), SCALE, id="число-в-заголовке"),
        pytest.param(answer(key_message="Каждый пятый — 20%", body=["Пункт"], refs=[RESULTS]), "20", id="число-в-ключевом-сообщении"),
        pytest.param(answer(visualization=chart([34, 100]), refs=[SCALE]), "100", id="точка-диаграммы-выдумана"),
        pytest.param(answer(visualization=table([["I кв. 2026", "35"]]), refs=[SCALE]), "35", id="ячейка-таблицы-выдумана"),
        pytest.param(answer(body=["Через программу прошли 1240 человек"], refs=[RESULTS]), SCALE, id="тысячи-без-пробела"),
        pytest.param(answer(body=["Пункт без чисел"]), "sourceRefs", id="тело-без-ссылок"),
        pytest.param(answer(body=["Пункт"], refs=["content.md#нет-такого"]), "sourceRefs", id="ссылка-вне-якорей"),
        pytest.param(answer(), "тело", id="пустой-слайд"),
        pytest.param(answer(body=["Пункт"], visualization=chart([34, 61]), refs=[SCALE]), "визуализац", id="тело-и-визуализация"),
        pytest.param(answer(visualization=chart([34]), refs=[SCALE]), "категор", id="точек-меньше-категорий"),
        pytest.param(answer(visualization=chart([34, 61], value_axis=None), refs=[SCALE]), "ос", id="оси-не-подписаны"),
        pytest.param(answer(visualization=table([["I кв. 2026"]]), refs=[SCALE]), "столб", id="строка-короче-шапки"),
    ],
)
def test_wrong_content_is_repaired_by_a_second_request(wrong: str, reason: str) -> None:
    provider = regular(s2=[wrong, content_answer()])
    result = generate(provider)

    requests = provider.for_slide(2)
    assert len(requests) == 2
    assert requests[1]["messages"][2] == {"role": "assistant", "content": wrong}
    assert reason in requests[1]["messages"][3]["content"]
    assert result.slides[1].body.items == ["Удержание выросло с 78% до 89%"]


def test_title_slide_with_a_body_is_repaired() -> None:
    """Тело перевело бы титульный слайд на контентный макет."""
    wrong = answer(headline="Программа наставничества", body=["Подзаголовок"], refs=[RESULTS])
    provider = regular(s1=[wrong, title_answer()])
    result = generate(provider)

    assert len(provider.for_slide(1)) == 2
    assert result.slides[0].body is None


def test_list_numbering_of_the_facts_is_not_a_fact() -> None:
    """Найдено живым прогоном: «4 часа» при «четырёх часах» в фактуре проверка
    отправила к разделу, где «4.» — номер этапа, и модель трижды не смогла
    это исправить."""
    content = (
        "## Этапы\n\n1. Заявка.\n2. Подбор пары — до 3 дней.\n3. Установочная встреча.\n"
        "4. Работа в паре.\n   10) Шесть встреч.\n\n## Суть\n\nОколо четырёх часов в месяц."
    )
    wrong = answer(body=["Пара тратит 4 часа в месяц, 10 встреч"], refs=["content.md#суть"])
    right = answer(body=["Пара тратит около четырёх часов в месяц"], refs=["content.md#суть"])
    provider = Provider({1: [title_answer()], 2: [wrong, right], 3: [right]})
    prompts = load_prompts()
    client = ModelClient(
        settings(),
        repair_prompt=prompts.get("skills/response-repair").text,
        transport=httpx.MockTransport(provider),
    )

    generate_content(structure(), ContentPack(brief=BRIEF, content=content), client, prompts)

    repair = provider.for_slide(2)[1]["messages"][3]["content"]
    assert "числа 4 нет в фактуре" in repair and "числа 10 нет в фактуре" in repair
    assert "content.md#этапы" not in repair
    # Число из самого пункта списка остаётся фактом, номер пункта — нет.
    assert fact_numbers(fact_sections(content)["content.md#этапы"]) == {"3"}


def test_number_found_in_a_cited_section_passes_whatever_its_spelling() -> None:
    provider = regular(s2=[answer(body=["Прошли 1 240 человек, пар стало 61"], refs=[SCALE])])
    result = generate(provider)

    assert len(provider.for_slide(2)) == 1
    assert result.slides[1].source_refs == [SCALE]


def test_content_that_stays_wrong_stops_with_the_stage_named_and_spends_no_more() -> None:
    wrong = answer(body=["Удержание выросло на 20%"], refs=[RESULTS])
    provider = regular(s1=[wrong] * 3)

    with pytest.raises(ModelError) as error:
        generate(provider, workers=1)

    assert error.value.stage == CONTENT_STAGE == "Содержание слайдов"
    # Частичной колоды нет (EF-8), а слайды, до которых очередь не дошла,
    # не запрашиваются: ответ на них выбросили бы.
    assert len(provider.requests) == 3
    assert {task(body)["slide"]["number"] for body in provider.requests} == {1}
