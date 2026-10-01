"""T-50: содержание слайдов со ссылками на источник.

По структуре колоды (T-49) модель пишет каждому слайду тело или
визуализацию и ссылки на разделы фактуры. Запрос — на слайд, потоки
параллельны: так заложено в бюджете `models-strategy.md`.

**Каждое число на слайде прослеживается до контент-пакета.** Ссылки на
источник ограничены схемой ответа — якорями разделов фактуры, а число,
которого нет в разделах, на которые слайд ссылается, не проходит проверку
контракта, и клиент T-47 повторяет запрос с перечнем ошибок.

**Колода не падает из-за одного слайда (T-61, вопрос T23).** Ссылку на
раздел, где число встречается одно, ставит код. Слайд, который за все
попытки так и не прошёл проверку, собирается из последнего ответа без
чисел, которых нет в фактуре, и называет убранное.

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

Удержание в первый год выросло с 78% до 89%, на 11 п. п. Пилот шёл весь 2026 год.

## Масштаб пилота

| Квартал | Активных пар |
|---|---|
| I кв. 2026 | 34 |
| II кв. 2026 | 61 |

Всего через программу прошли 1 240 человек.
"""
RESULTS = "content.md#результаты-пилота"
SCALE = "content.md#масштаб-пилота"
FRAME = "content.md#фактура"
"""Раздел без чисел: ссылка на него не прослеживает ни одного числа."""


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
    какой запрос придёт первым, решают потоки. Число в очереди — код ответа
    провайдера без текста: сбой на его стороне.
    """

    def __init__(self, answers: dict[int, list[str | int]]) -> None:
        self.answers = {number: list(queue) for number, queue in answers.items()}
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        number = task(body)["slide"]["number"]
        with self.lock:
            self.requests.append(body)
            content = self.answers[number].pop(0)
        if isinstance(content, int):
            return httpx.Response(content, text="сбой провайдера")
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
        pytest.param(answer(body=["Пилот шёл в 2026 году"], refs=[FRAME]), RESULTS, id="число-в-нескольких-разделах"),
        pytest.param(answer(headline="Итоги 2026 года", body=["Пункт"], refs=[FRAME]), SCALE, id="число-в-заголовке"),
        pytest.param(answer(key_message="Каждый пятый — 20%", body=["Пункт"], refs=[RESULTS]), "20", id="число-в-ключевом-сообщении"),
        pytest.param(answer(visualization=chart([34, 100]), refs=[SCALE]), "100", id="точка-диаграммы-выдумана"),
        pytest.param(answer(visualization=table([["I кв. 2026", "35"]]), refs=[SCALE]), "35", id="ячейка-таблицы-выдумана"),
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


def test_number_from_another_section_may_be_cited_or_dropped() -> None:
    """Найдено живым прогоном: у слайда плана «5 частей» совпало с числом
    раздела «Контекст». Подсказка «сошлись на раздел» была бессмысленной для
    плана, и модель дважды ушла в рассуждение до лимита в 8000 токенов.

    Ссылку за план код не ставит (T-61): он сделан из состава колоды, и
    совпадение числа с фактурой у него случайно."""
    wrong = answer(body=["Колода из 61 части"])
    provider = regular(s2=[wrong, answer(body=["Результаты пилота"])])
    result = generate(provider, structure("title", "agenda", "closing"))

    repair = provider.for_slide(2)[1]["messages"][3]["content"]
    assert SCALE in repair and "без числа" in repair
    assert result.slides[1].source_refs == []


def test_title_slide_with_a_body_is_repaired() -> None:
    """Тело перевело бы титульный слайд на контентный макет."""
    wrong = answer(headline="Программа наставничества", body=["Подзаголовок"], refs=[RESULTS])
    provider = regular(s1=[wrong, title_answer()])
    result = generate(provider)

    assert len(provider.for_slide(1)) == 2
    assert result.slides[0].body is None


def test_list_numbering_of_the_facts_is_not_a_fact() -> None:
    """Найдено живым прогоном: на число 4, которого в фактуре нет, проверка
    отправила к разделу, где «4.» — номер этапа, и модель трижды не смогла
    это исправить."""
    content = (
        "## Этапы\n\n1. Заявка.\n2. Подбор пары — до 3 дней.\n3. Установочная встреча.\n"
        "4. Работа в паре.\n   10) Шесть встреч.\n\n## Суть\n\nПара тратит пару часов в месяц."
    )
    wrong = answer(body=["Пара тратит 4 часа в месяц, 10 встреч"], refs=["content.md#суть"])
    right = answer(body=["Пара тратит пару часов в месяц"], refs=["content.md#суть"])
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
    assert fact_numbers(fact_sections(content)["content.md#этапы"]) == {"3", "6"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Пилот окупился за четыре квартала", {"4"}),
        ("Процесс состоит из пяти этапов, пара работает три месяца", {"5", "3"}),
        ("по две пары одновременно, один день теории", {"2", "1"}),
        ("Каждый пятый новичок увольнялся", set()),
        ("около четырёх часов в месяц", {"4"}),
    ],
)
def test_number_written_in_words_in_the_facts_may_go_to_the_slide_in_digits(text: str, expected: set[str]) -> None:
    """Найдено живым прогоном: «4 квартала» при «четыре квартала» в фактуре —
    то же число, а не пересчёт; модель не отказывалась от цифры три попытки
    подряд. Порядковое «пятый» числом не считается: «каждый пятый» — не «5»."""
    assert fact_numbers(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Затраты — 2,8 млн ₽, эффект — 9,4 млн ₽", {"2.8", "2800000", "9.4", "9400000"}),
        ("месяц стоит 49 тыс. ₽", {"49", "49000"}),
        ("оборот 1,5 млрд", {"1.5", "1500000000"}),
    ],
)
def test_number_with_a_scale_word_may_go_to_the_slide_in_full(text: str, expected: set[str]) -> None:
    """Найдено живым прогоном: «2,8 млн ₽» ушло в точку диаграммы как 2800000 —
    то же число; модель три попытки подряд не отказывалась от такой записи."""
    assert fact_numbers(text) == expected


def test_agenda_needs_no_source_it_is_made_of_the_deck() -> None:
    """Пункты плана — части колоды, а не факты; ссылку модель ставила наугад."""
    agenda = answer(body=["Результаты пилота", "Запрос"])
    provider = regular(s2=[agenda])
    result = generate(provider, structure("title", "agenda", "closing"))

    assert len(provider.for_slide(2)) == 1
    assert result.slides[1].source_refs == []


def test_number_found_in_a_cited_section_passes_whatever_its_spelling() -> None:
    provider = regular(s2=[answer(body=["Прошли 1 240 человек, пар стало 61"], refs=[SCALE])])
    result = generate(provider)

    assert len(provider.for_slide(2)) == 1
    assert result.slides[1].source_refs == [SCALE]


# --- Колода не падает из-за одного слайда (T-61) ----------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        pytest.param(answer(body=["Удержание 89%"], refs=[FRAME]), [FRAME, RESULTS], id="тело"),
        pytest.param(answer(body=["Удержание 89%"]), [RESULTS], id="ссылок-не-было"),
        pytest.param(answer(headline="Пар стало 61", body=["Пункт"], refs=[RESULTS]), [RESULTS, SCALE], id="заголовок"),
        pytest.param(answer(body=["Через программу прошли 1240 человек"], refs=[RESULTS]), [RESULTS, SCALE], id="тысячи-без-пробела"),
        # 2026 есть в двух разделах, но 34 — только в масштабе: ссылка на него
        # прослеживает и год.
        pytest.param(answer(visualization=chart([34, 61])), [SCALE], id="диаграмма"),
    ],
)
def test_number_found_in_one_section_only_gets_its_anchor_from_code(given: str, expected: list[str]) -> None:
    """Вариант (а) вопроса T23 — урок T-51 и T-52: модель пишет, решает код.
    Число, которое есть ровно в одном разделе фактуры, оттуда и взято, и
    повтор ради ссылки на этот раздел стоил бы запроса. Найдено T-59: с каждым
    повтором модель добавляла одну ссылку из нескольких нужных."""
    provider = regular(s2=[given])
    result = generate(provider)

    assert len(provider.for_slide(2)) == 1
    assert result.slides[1].source_refs == expected


def test_slide_that_stays_wrong_keeps_what_is_traced_and_names_what_is_dropped() -> None:
    """Вариант (б) вопроса T23: колода собирается, слайд — из последнего ответа.

    Число, которого нет в фактуре, на слайд не попадает: пункт с ним убран и
    назван, заголовок и ключевое сообщение остаются из структуры колоды.
    Число, которое в фактуре есть, но в нескольких разделах, остаётся — то
    ли это число, решает человек по предупреждению проверки текста."""
    wrong = answer(
        headline="Удержание выросло на 20%",
        key_message="Каждый пятый остался бы — 20%",
        body=["Пилот шёл в 2026 году", "Отток упал на 20%"],
        refs=[FRAME],
    )
    provider = regular(s2=[wrong] * 3)
    result = generate(provider)

    assert len(provider.for_slide(2)) == 3
    degraded = result.slides[1]
    assert (degraded.headline, degraded.key_message) == ("Заголовок 2", "Мысль 2")
    assert degraded.body is not None and degraded.body.items == ["Пилот шёл в 2026 году"]
    assert degraded.source_refs == [FRAME]
    assert [(item.text, item.numbers) for item in degraded.omitted] == [("Отток упал на 20%", ["20"])]
    # Остальные слайды написаны как обычно.
    assert result.slides[2].body.items == ["Удержание выросло с 78% до 89%"]
    assert result.slides[2].omitted == []


def test_visualization_with_an_invented_number_is_dropped_whole() -> None:
    """Диаграмма без одной точки или таблица без ячейки врут сильнее, чем
    слайд без них."""
    wrong = answer(visualization=table([["I кв. 2026", "34"], ["II кв. 2026", "35"]]), refs=[SCALE])
    provider = regular(s2=[wrong] * 3)
    result = generate(provider)

    degraded = result.slides[1]
    assert degraded.visualization is None and degraded.body is None
    [dropped] = degraded.omitted
    assert dropped.numbers == ["35"] and "Квартал" in dropped.text


@pytest.mark.parametrize(
    ("role", "wrong", "body", "visual"),
    [
        pytest.param("title", answer(body=["Подзаголовок"], refs=[RESULTS]), None, False, id="титул-с-телом"),
        pytest.param("data", answer(body=["Пункт"], visualization=chart([34, 61]), refs=[SCALE]), None, True, id="тело-и-визуализация"),
        pytest.param("data", answer(body=["Пункт"], visualization=chart([34]), refs=[SCALE]), ["Пункт"], False, id="визуализация-не-собрана"),
    ],
)
def test_degraded_slide_keeps_the_shape_the_layout_expects(
    role: str, wrong: str, body: list[str] | None, visual: bool
) -> None:
    """Слайд, собранный из неудачного ответа, вёрстка принимает как обычный:
    у титула нет тела, у слайда не бывает и тела, и визуализации — так их и
    свёрстали бы, — а несобранная визуализация не показывается."""
    provider = Provider({1: [wrong] * 3, 2: [content_answer()]})
    result = generate(provider, structure(role, "closing"))

    slide = result.slides[0]
    assert (slide.body.items if slide.body else None) == body
    assert (slide.visualization is not None) == visual


def test_answer_that_never_parsed_stops_with_the_stage_named_and_spends_no_more() -> None:
    """Модель недоступна или отвечает не по схеме — ошибка этапа (EF-8):
    сохранять из такого ответа нечего. Слайды, до которых очередь не дошла,
    не запрашиваются: их ответы выбросили бы."""
    provider = regular(s1=["не JSON"] * 3)

    with pytest.raises(ModelError) as error:
        generate(provider, workers=1)

    assert error.value.stage == CONTENT_STAGE == "Содержание слайдов"
    assert len(provider.requests) == 3
    assert {task(body)["slide"]["number"] for body in provider.requests} == {1}


def test_provider_failure_after_an_answer_keeps_that_answer() -> None:
    """Так оборвался третий запрос в диагностике T-59: ответ второй попытки
    годится для слайда, хотя последней попытке провайдер не ответил."""
    wrong = answer(body=["Пилот шёл в 2026 году", "Отток упал на 20%"], refs=[FRAME])
    provider = regular(s2=[wrong, wrong, 503])
    result = generate(provider)

    assert result.slides[1].body.items == ["Пилот шёл в 2026 году"]
