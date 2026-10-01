"""T-51: аудит текста, подслой 4a.

Девять проверок раздела «Валидация контента» Приложения 1, кроме вопросов 5
и 6: пятый — это `integrity.empty_slide`, шестой смотрит на картинки и
относится к аудиту визуала (T-52). Выполняются один раз на колоду до вёрстки:
текст во всех трёх вариантах один и тот же.

**Модели — один запрос на колоду**, все вопросы разом (бюджет
`models-strategy.md`). Проверки читают свою часть ответа и остаются
самостоятельными единицами реестра. Без модели смысловые проверки не
выполняются и названы в отчёте пропущенными — пустой список находок не
выдаётся за чистый текст.

**Гибридные проверки** — числа и служебный мусор — сначала ищут
детерминированно, класс находки `file`; модель добавляет только то, чего
поиск не видит, класс находки `model`.

Провайдер подменён `httpx.MockTransport`: тест проверяет код, а не модель.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from dpd.audit import REGISTRY, discover
from dpd.audit.checks.content import (
    check_body_mismatch_headline,
    check_headline_no_conclusion,
    check_mixed_language,
    check_not_summarizable,
    check_service_garbage,
    check_slides_disconnected,
    check_table_rows_irrelevant,
    check_typos,
    check_unsourced_numbers,
)
from dpd.audit.textual import (
    TEXT_STAGE,
    attach,
    audit_text,
    review_contract,
    review_text,
)
from dpd.generation import ContentPack
from dpd.llm import ModelClient, ModelError, ModelSettings, load_prompts
from dpd.models import AuditReport, Finding, PresentationStructure, StructureSlide
from dpd.models.structure import (
    ChartSeries,
    ChartSpec,
    Omission,
    SlideBody,
    TableSpec,
    Visualization,
)

BRIEF = "# Бриф\n\nПросим решение о масштабировании пилота."
CONTENT = """# Фактура

## Результаты пилота

Удержание в первый год выросло с 78% до 89%, на 11 п. п.

## Масштаб пилота

| Квартал | Активных пар |
|---|---|
| I кв. 2026 | 34 |
| II кв. 2026 | 61 |
"""
RESULTS = "content.md#результаты-пилота"
SCALE = "content.md#масштаб-пилота"
PACK = ContentPack(brief=BRIEF, content=CONTENT)

TEXT_CHECKS = {
    "content.headline_no_conclusion": ("model", "advice"),
    "content.body_mismatch_headline": ("model", "warning"),
    "content.not_summarizable": ("model", "advice"),
    "content.unsourced_numbers": ("file", "critical"),
    "content.service_garbage": ("file", "critical"),
    "content.typos": ("model", "warning"),
    "content.mixed_language": ("file", "warning"),
    "content.table_rows_irrelevant": ("model", "advice"),
    "content.slides_disconnected": ("model", "advice"),
}
MODEL_ONLY = [check_id for check_id, (kind, _) in TEXT_CHECKS.items() if kind == "model"]


def slide(number: int, role: str = "data", headline: str = "Заголовок", **fields: object) -> StructureSlide:
    return StructureSlide(id=f"s{number}", role=role, headline=headline, **fields)  # type: ignore[arg-type]


def bullets(*items: str) -> SlideBody:
    return SlideBody(items=list(items))


def deck(*slides: StructureSlide) -> PresentationStructure:
    return PresentationStructure(slides=list(slides))


def sample() -> PresentationStructure:
    """Титульный, слайд с числами из фактуры и слайд с таблицей."""
    return deck(
        slide(1, "title", "Программа «Навигатор»"),
        slide(2, headline="Удержание выросло на 11 п. п.", body=bullets("Было 78%, стало 89%"), source_refs=[RESULTS]),
        slide(
            3,
            headline="Программа растёт каждый квартал",
            visualization=Visualization(
                kind="table",
                table=TableSpec(headers=["Квартал", "Пар"], rows=[["I кв.", "34"], ["II кв.", "61"]]),
            ),
            source_refs=[SCALE],
        ),
    )


def fine(structure: PresentationStructure) -> dict:
    """Ответ модели, у которой нет замечаний ни к одному слайду."""
    ok = {"ok": True, "reason": ""}
    return {
        "slides": [
            {
                "id": item.id,
                "headlineKind": "service" if item.role == "title" else "claim",
                "bodySupportsHeadline": ok,
                "oneSentence": ok,
                "unsupportedClaims": [],
                "serviceText": [],
                "typos": [],
                "offPointItems": [],
                "followsPrevious": ok,
            }
            for item in structure.slides
        ]
    }


def with_remarks(structure: PresentationStructure, slide_id: str, **remarks: object) -> dict:
    answer = fine(structure)
    target = next(item for item in answer["slides"] if item["id"] == slide_id)
    target.update(remarks)
    return answer


def review_of(structure: PresentationStructure, answer: dict):
    return review_contract(structure).model_validate(answer)


def bad(reason: str) -> dict:
    return {"ok": False, "reason": reason}


# --- Реестр ----------------------------------------------------------------------


def test_every_text_check_of_the_checklist_is_registered() -> None:
    discover()
    for check_id, (kind, severity) in TEXT_CHECKS.items():
        registered = REGISTRY.get(check_id)
        assert registered is not None, f"{check_id} не зарегистрирована"
        spec = registered.spec
        assert (spec.check_class, spec.severity) == (kind, severity), check_id
        assert (spec.category, spec.fixability, spec.sublayer) == ("content", "semantic", "4a"), check_id


# --- Числа: детерминированная часть ----------------------------------------------


def test_numbers_from_the_cited_sections_are_clean() -> None:
    assert check_unsourced_numbers(sample(), PACK) == []


def test_number_missing_from_the_materials_is_critical() -> None:
    structure = deck(slide(1, body=bullets("Удержание выросло до 93%"), source_refs=[RESULTS]))

    [finding] = check_unsourced_numbers(structure, PACK)

    assert (finding.severity, finding.check_class, finding.slide_number) == ("critical", "file", 1)
    assert finding.evidence["number"] == "93"
    assert "93" in finding.message


def test_number_from_another_section_is_a_warning() -> None:
    """Число есть в материалах, но не в разделе, на который опирается слайд:
    совпадение бывает случайным, и решать, то ли это число, — человеку."""
    structure = deck(slide(1, body=bullets("Активных пар — 61"), source_refs=[RESULTS]))

    [finding] = check_unsourced_numbers(structure, PACK)

    assert (finding.severity, finding.check_class) == ("warning", "file")
    assert finding.evidence["sections"] == [SCALE]


def test_slide_without_sources_is_checked_against_all_materials() -> None:
    """У плана, написанного человеком, ссылок на источник нет."""
    structure = deck(slide(1, body=bullets("Активных пар — 61")))

    assert check_unsourced_numbers(structure, PACK) == []


def test_chart_points_and_table_cells_are_traced() -> None:
    chart = ChartSpec(categories=["I кв.", "II кв."], series=[ChartSeries(name="Пары", points=[34, 62])])
    structure = deck(slide(1, visualization=Visualization(kind="chart", chart=chart), source_refs=[SCALE]))

    [finding] = check_unsourced_numbers(structure, PACK)

    assert finding.evidence["number"] == "62"


def test_text_dropped_by_the_writer_is_named() -> None:
    """T-61: слайд, который модель за все попытки так и не написала без
    выдуманных чисел, собирается без них. Молча терять пункт нельзя: человек
    узнаёт, что убрано и почему, и решает, вписать ли его сам."""
    omitted = [Omission(text="Отток упал на 20%", numbers=["20"]), Omission(text="таблица: Квартал, Пар", numbers=["35", "40"])]
    structure = deck(slide(1, body=bullets("Было 78%, стало 89%"), source_refs=[RESULTS], omitted=omitted))

    first, second = check_unsourced_numbers(structure, PACK)

    assert (first.severity, first.check_class, first.slide_number) == ("warning", "file", 1)
    assert "Отток упал на 20%" in first.message and "числа 20 нет" in first.message
    assert "чисел 35, 40 нет" in second.message
    assert first.evidence == {"slideId": "s1", "omitted": "Отток упал на 20%", "numbers": ["20"]}


def test_key_message_is_not_on_the_slide_and_is_not_checked() -> None:
    structure = deck(slide(1, key_message="Через год пар станет 200", body=bullets("Пар — 61"), source_refs=[SCALE]))

    assert check_unsourced_numbers(structure, PACK) == []


def test_number_found_by_search_is_not_repeated_as_a_claim() -> None:
    """Живой прогон: модель повторила строку с выдуманным числом как
    «утверждение без источника», хотя числа ей проверять не велено. Одна и та
    же строка не предъявляется дважды — остаётся воспроизводимая находка."""
    structure = deck(slide(1, body=bullets("Удовлетворённость наставников — 97%"), source_refs=[RESULTS]))
    review = review_of(structure, with_remarks(structure, "s1", unsupportedClaims=["Удовлетворённость наставников — 97%"]))

    findings = check_unsourced_numbers(structure, PACK, review=review)

    assert [f.check_class for f in findings] == ["file"]


def test_claims_without_numbers_come_from_the_model() -> None:
    structure = sample()
    review = review_of(structure, with_remarks(structure, "s2", unsupportedClaims=["Было 78%, стало 89%"]))

    findings = check_unsourced_numbers(structure, PACK, review=review)

    assert [(f.check_class, f.slide_number) for f in findings] == [("model", 2)]


# --- Служебный мусор ---------------------------------------------------------------


def test_service_markers_are_found_without_a_model() -> None:
    structure = deck(slide(1, body=bullets("Заметки докладчика: здесь пошутить", "Итог {total} за год")))

    findings = check_service_garbage(structure, markers=["заметки докладчика"], endings=["..."])

    assert [(f.check_class, f.severity, f.slide_number) for f in findings] == [("file", "critical", 1)] * 2


def test_text_cut_off_mid_sentence_is_service_garbage() -> None:
    structure = deck(slide(1, body=bullets("Наставники нужны, потому что...")))

    [finding] = check_service_garbage(structure, markers=[], endings=["..."])

    assert finding.check_class == "file"


def test_service_text_noticed_by_the_model() -> None:
    structure = deck(slide(1, body=bullets("Здесь стоит рассказать про бюджет")))
    review = review_of(structure, with_remarks(structure, "s1", serviceText=["Здесь стоит рассказать про бюджет"]))

    [finding] = check_service_garbage(structure, markers=[], endings=[], review=review)

    assert finding.check_class == "model"


# --- Язык ----------------------------------------------------------------------------


def test_phrase_in_another_language_is_found() -> None:
    structure = deck(
        slide(1, headline="Наставничество окупилось", body=bullets("Retention grew after the pilot", "Пар стало вдвое больше")),
    )

    [finding] = check_mixed_language(structure, min_words=3, max_share=0.5)

    assert (finding.check_class, finding.slide_number) == ("file", 1)
    assert "Retention" in finding.message


def test_product_names_inside_a_phrase_are_not_another_language() -> None:
    structure = deck(slide(1, headline="Сборка в LibreOffice и Streamlit занимает минуту", body=bullets("VK WorkSpace")))

    assert check_mixed_language(structure, min_words=3, max_share=0.5) == []


# --- Смысловые вопросы: ответ модели становится находками -------------------------


def test_headline_naming_a_topic_is_reported() -> None:
    """Модель классифицирует заголовок, а не отвечает «да/нет»: живой прогон
    показал, что в вопросе «заголовок — вывод?» она путает, что значит
    `ok: false`. Текст замечания пишет программа."""
    structure = sample()
    review = review_of(structure, with_remarks(structure, "s3", headlineKind="topic"))

    [finding] = check_headline_no_conclusion(structure, review)

    assert (finding.check_class, finding.severity, finding.slide_number) == ("model", "advice", 3)
    assert "«Программа растёт каждый квартал»" in finding.message


def test_service_slides_need_no_conclusion_in_the_headline() -> None:
    structure = sample()
    review = review_of(structure, with_remarks(structure, "s1", headlineKind="topic"))

    assert check_headline_no_conclusion(structure, review) == []


@pytest.mark.parametrize(
    ("check", "field", "slide_id"),
    [
        (check_body_mismatch_headline, "bodySupportsHeadline", "s2"),
        (check_not_summarizable, "oneSentence", "s2"),
        (check_slides_disconnected, "followsPrevious", "s3"),
    ],
)
def test_yes_no_questions_become_findings(check, field: str, slide_id: str) -> None:
    structure = sample()
    review = review_of(structure, with_remarks(structure, slide_id, **{field: bad("Причина для автора")}))

    [finding] = check(structure, review)

    assert finding.slide_number == int(slide_id[1:])
    assert "Причина для автора" in finding.message
    assert check(structure, review_of(structure, fine(structure))) == []


def test_typo_names_the_fix() -> None:
    structure = deck(slide(1, body=bullets("Наставнеки выходят раньше")))
    review = review_of(structure, with_remarks(structure, "s1", typos=[{"wrong": "Наставнеки", "right": "Наставники"}]))

    [finding] = check_typos(structure, review)

    assert "Наставнеки" in finding.message and "Наставники" in finding.message
    assert finding.evidence == {"slideId": "s1", "wrong": "Наставнеки", "right": "Наставники"}


def test_number_is_not_a_typo() -> None:
    """Живой прогон: модель «исправила» выдуманное число как опечатку —
    «97%» на «81%» из соседней строки фактуры. Числа сверяет поиск по
    материалам, воспроизводимо; догадка модели о верном числе — не опечатка."""
    structure = deck(slide(1, body=bullets("Удовлетворённость наставников — 97%")))
    review = review_of(structure, with_remarks(structure, "s1", typos=[{"wrong": "97%", "right": "81%"}]))

    assert check_typos(structure, review) == []


def test_table_row_off_the_point_is_reported() -> None:
    structure = sample()
    review = review_of(structure, with_remarks(structure, "s3", offPointItems=["II кв."]))

    [finding] = check_table_rows_irrelevant(structure, review)

    assert (finding.slide_number, finding.evidence["item"]) == (3, "II кв.")


# --- Контракт ответа ----------------------------------------------------------------


def test_every_slide_gets_exactly_one_answer() -> None:
    structure = sample()
    answer = fine(structure)
    answer["slides"][2]["id"] = "s2"

    with pytest.raises(ValidationError, match="s3"):
        review_of(structure, answer)


def test_problem_without_a_reason_is_rejected() -> None:
    structure = sample()

    with pytest.raises(ValidationError, match="reason"):
        review_of(structure, with_remarks(structure, "s2", oneSentence={"ok": False, "reason": " "}))


def test_quote_that_is_not_on_the_slide_is_rejected() -> None:
    """Цитата — адрес находки: выдуманная указывала бы в никуда."""
    structure = sample()

    with pytest.raises(ValidationError, match="Наставнеки"):
        review_of(structure, with_remarks(structure, "s2", typos=[{"wrong": "Наставнеки", "right": "Наставники"}]))


def test_slide_ids_are_constrained_by_the_schema() -> None:
    schema = json.dumps(review_contract(sample()).model_json_schema(by_alias=True), ensure_ascii=False)

    assert '"s3"' in schema


# --- Запрос к модели ------------------------------------------------------------------


def settings() -> ModelSettings:
    return ModelSettings(
        base_url="https://provider.test/v1",
        api_key="test",
        model="test-model",
        temperature=0,
        seed=1,
        max_tokens=100,
        timeout_sec=5,
        max_attempts=2,
        retry_delay_sec=0,
    )


class Provider:
    def __init__(self, *answers: dict | str) -> None:
        self.answers = list(answers)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        answer = self.answers.pop(0)
        content = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        message = {"role": "assistant", "content": content}
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}]})


def client(provider: Provider) -> ModelClient:
    prompts = load_prompts()
    return ModelClient(settings(), repair_prompt=prompts.get("skills/response-repair").text, transport=httpx.MockTransport(provider))


def test_whole_deck_is_reviewed_with_one_request() -> None:
    structure = sample()
    provider = Provider(fine(structure))
    prompts = load_prompts()

    review_text(structure, PACK, client(provider), prompts)

    [request] = provider.requests
    system, user = (message["content"] for message in request["messages"])
    assert system == prompts.get("skills/content-validator").text
    assert f"[{RESULTS}]" in user and BRIEF in user
    task = json.loads(user.rsplit("\n\n", 1)[1])
    assert [item["id"] for item in task["slides"]] == ["s1", "s2", "s3"]
    assert task["slides"][2]["table"]["rows"] == [["I кв.", "34"], ["II кв.", "61"]]
    assert task["slides"][1]["sourceRefs"] == [RESULTS]


def test_answer_failing_the_contract_stops_the_stage() -> None:
    structure = sample()
    provider = Provider("{}", "{}")

    with pytest.raises(ModelError) as error:
        review_text(structure, PACK, client(provider), load_prompts())

    assert error.value.stage == TEXT_STAGE


# --- Подслой целиком -------------------------------------------------------------------


def test_without_a_model_the_text_checks_are_named_skipped() -> None:
    report = audit_text(sample(), PACK)

    assert set(MODEL_ONLY) <= set(report.checks_skipped)
    assert {"content.unsourced_numbers", "content.service_garbage", "content.mixed_language"} <= set(report.checks_run)
    # Детерминированная половина гибридной проверки выполнена, смысловая — нет.
    assert {"content.unsourced_numbers:model", "content.service_garbage:model"} <= set(report.checks_skipped)
    assert "integrity.duplicate_slides" in report.checks_run


def test_with_a_model_every_text_check_runs() -> None:
    structure = sample()
    answer = with_remarks(structure, "s3", headlineKind="topic")

    report = audit_text(structure, PACK, client(Provider(answer)), load_prompts())

    assert set(TEXT_CHECKS) <= set(report.checks_run)
    assert not [item for item in report.checks_skipped if item.startswith("content.")]
    assert [f.check_id for f in report.findings] == ["content.headline_no_conclusion"]


def test_without_materials_numbers_cannot_be_traced() -> None:
    report = audit_text(sample())

    assert "content.unsourced_numbers" in report.checks_skipped


# --- Отчёт варианта несёт находки аудита текста -----------------------------------------


def finding(check_id: str, number: int, *, severity: str = "warning", slide_id: str | None = None) -> Finding:
    evidence = {"slideId": slide_id} if slide_id else {}
    return Finding.model_validate(
        {
            "checkId": check_id,
            "category": check_id.split(".")[0],
            "class": "file",
            "severity": severity,
            "fixability": "none",
            "message": check_id,
            "slideNumber": number,
            "evidence": evidence,
        }
    )


def test_text_report_is_attached_to_the_variant_report() -> None:
    variant = AuditReport(
        run_id="r",
        variant="A",
        findings=[finding("density.too_many_bullets", 2), finding("integrity.duplicate_slides", 3)],
        checks_run=["density.too_many_bullets", "integrity.duplicate_slides", "content.service_garbage"],
        checks_skipped=["content.unsourced_numbers"],
    )
    text = AuditReport(
        run_id="t",
        findings=[finding("integrity.duplicate_slides", 3), finding("content.typos", 2, slide_id="s2")],
        checks_run=["integrity.duplicate_slides", "content.service_garbage", "content.unsourced_numbers", "content.typos"],
        checks_skipped=["content.unsourced_numbers:model"],
    )

    merged = attach(variant, text)

    # Проверки текста один раз на колоду: их результат берётся из аудита текста,
    # а не дублируется прогоном варианта.
    assert [f.check_id for f in merged.findings] == [
        "density.too_many_bullets",
        "integrity.duplicate_slides",
        "content.typos",
    ]
    assert merged.findings[0] == variant.findings[0].model_copy(update={"id": "f1"})
    assert [f.id for f in merged.findings] == ["f1", "f2", "f3"]
    assert sorted(merged.checks_run) == sorted(
        ["density.too_many_bullets", "integrity.duplicate_slides", "content.service_garbage", "content.unsourced_numbers", "content.typos"]
    )
    assert merged.checks_skipped == ["content.unsourced_numbers:model"]
    assert merged.variant == "A"
    assert attach(variant, None) == variant


def test_attached_finding_follows_its_slide_when_slides_shift() -> None:
    """После переноса части слайда на новый номера следующих слайдов сдвигаются,
    а находка аудита текста привязана к слайду замысла, не к номеру."""
    from dpd.models import Canvas, RenderedPresentation, Slide

    shifted = RenderedPresentation(
        variant="A",
        template_hash="x",
        canvas=Canvas(width_emu=1, height_emu=1),
        slides=[Slide(id=item, layout_id="l") for item in ("s1", "s2", "s2-2", "s3")],
    )
    text = AuditReport(run_id="t", findings=[finding("content.typos", 3, slide_id="s3")], checks_run=["content.typos"])

    merged = attach(AuditReport(run_id="r"), text, shifted)

    assert merged.findings[0].slide_number == 4
