"""T-52: аудит визуала, подслой 4c.

Две проверки, которым нужно изображение слайда: вопрос 6 Приложения 1 —
картинки и пиктограммы по теме слайда (`content.irrelevant_imagery`), — и
визуальная читаемость сверх набора ТЗ (`content.visual_readability`).
Остальные вопросы валидации решаются по тексту (T-51) и по данным файла.

**Один запрос на слайд, все вопросы разом** (критерий приёмки). Модель видит
изображение и список того, что на слайд положила вёрстка. Замечание о
читаемости называет номер элемента из списка, и находка получает его блок:
подсветка покажет место, а не весь слайд.

**Смотрится выбранный вариант** — после выбора (ADR-0002, п. 4); батч сдачи
смотрит все. Без модели проверки названы в отчёте пропущенными.

Провайдер подменён `httpx.MockTransport`: тест проверяет код, а не модель.
"""

from __future__ import annotations

import base64
import json
import threading
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from dpd.audit import REGISTRY, discover
from dpd.audit.textual import attach
from dpd.audit.visual import VISUAL_STAGE, audit_visual, look_contract, review_visual
from dpd.llm import ModelClient, ModelError, ModelSettings, load_prompts
from dpd.models import AuditReport, Finding, RenderedPresentation
from dpd.models.common import Bounds, Canvas, TextRun
from dpd.models.rendered import RenderedChart, RenderedElement, RenderedTable, Slide
from dpd.models.structure import AxisTitles, ChartSeries

READABILITY = "content.visual_readability"
IMAGERY = "content.irrelevant_imagery"

BOX = Bounds(x=0.1, y=0.1, w=0.8, h=0.3)


def text(slot: str, *lines: str) -> RenderedElement:
    return RenderedElement(slot_id=slot, kind="text", bounds=BOX, runs=[TextRun(text=line) for line in lines])


def sample() -> RenderedPresentation:
    table = RenderedTable(headers=["Показатель", "До", "После"], rows=[["Удержание", "78%", "89%"]])
    chart = RenderedChart(
        chart_type="column",
        categories=["I кв.", "II кв."],
        series=[ChartSeries(name="Пары", points=[34, 61])],
        axis_titles=AxisTitles(category="Квартал", value="Пары"),
    )
    return RenderedPresentation(
        variant="A",
        template_hash="t",
        canvas=Canvas(width_emu=12192000, height_emu=6858000),
        slides=[
            Slide(id="s1", layout_id="l1", elements=[text("title", "Наставничество окупилось")]),
            Slide(
                id="s2",
                layout_id="l2",
                elements=[
                    text("title", "Удержание выросло"),
                    RenderedElement(slot_id="body", kind="table", bounds=BOX, table=table),
                ],
            ),
            Slide(
                id="s3",
                layout_id="l2",
                elements=[
                    text("title", "Пар стало вдвое больше"),
                    RenderedElement(slot_id="body", kind="chart", bounds=BOX, chart=chart),
                ],
            ),
        ],
    )


def pictures(tmp_path: Path, count: int = 3) -> list[Path]:
    paths = []
    for number in range(1, count + 1):
        path = tmp_path / f"slide-{number:03d}.png"
        path.write_bytes(f"png-{number}".encode())
        paths.append(path)
    return paths


def clean() -> dict:
    return {"readability": [], "offTopicPictures": []}


# --- Модель-подделка --------------------------------------------------------------


def settings() -> ModelSettings:
    return ModelSettings(
        base_url="https://provider.test/v1",
        api_key="test",
        model="test-vlm",
        temperature=0,
        seed=1,
        max_tokens=100,
        timeout_sec=5,
        max_attempts=2,
        retry_delay_sec=0,
    )


def card(body: dict) -> dict:
    """Карточка слайда из запроса: текстовая часть сообщения пользователя."""
    [part] = [item for item in body["messages"][1]["content"] if item["type"] == "text"]
    return json.loads(part["text"])


class Provider:
    """Поддельный VLM: отвечает по номеру слайда из карточки.

    Слайды смотрятся параллельно, и порядок запросов не определён — ответ
    выбирается по слайду, а не по очереди.
    """

    def __init__(self, answers: dict[int, list[dict | str]] | None = None) -> None:
        self.answers = answers or {}
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        with self.lock:
            self.requests.append(body)
            queue = self.answers.get(card(body)["number"])
            answer = queue.pop(0) if queue else clean()
        content = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        message = {"role": "assistant", "content": content}
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}]})

    def by_slide(self) -> dict[int, dict]:
        return {card(body)["number"]: body for body in self.requests}


def client(provider: Provider) -> ModelClient:
    prompts = load_prompts()
    return ModelClient(settings(), repair_prompt=prompts.get("skills/response-repair").text, transport=httpx.MockTransport(provider))


# --- Реестр -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("check_id", "severity", "fixability"),
    [(IMAGERY, "warning", "semantic"), (READABILITY, "advice", "lossy")],
)
def test_both_visual_checks_of_the_checklist_are_registered(check_id: str, severity: str, fixability: str) -> None:
    """Классификация — из чек-листа `docs/07-testing/audit-checklist.md`."""
    discover()
    spec = REGISTRY.get(check_id).spec

    assert (spec.category, spec.check_class, spec.sublayer) == ("content", "model", "4c")
    assert (spec.severity, spec.fixability) == (severity, fixability)


# --- Запрос к модели ----------------------------------------------------------------


def test_every_slide_is_looked_at_with_one_request(tmp_path: Path) -> None:
    provider = Provider()
    prompts = load_prompts()

    review_visual(sample(), pictures(tmp_path), client(provider), prompts)

    assert sorted(provider.by_slide()) == [1, 2, 3]
    for number, body in provider.by_slide().items():
        system, user = body["messages"]
        assert system["content"] == prompts.get("skills/visual-auditor").text
        [image] = [item for item in user["content"] if item["type"] == "image_url"]
        assert image["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(f"png-{number}".encode()).decode()


def test_card_lists_what_the_layout_put_on_the_slide(tmp_path: Path) -> None:
    """Модель сравнивает изображение со списком: текст, который есть в списке,
    но не виден на изображении, — замечание. Без списка его не заметить."""
    provider = Provider()

    review_visual(sample(), pictures(tmp_path), client(provider), load_prompts())

    slides = {number: card(body) for number, body in provider.by_slide().items()}
    assert slides[1]["elements"] == [{"n": 1, "kind": "text", "text": ["Наставничество окупилось"]}]
    assert slides[2]["elements"][1] == {
        "n": 2,
        "kind": "table",
        "headers": ["Показатель", "До", "После"],
        "rows": [["Удержание", "78%", "89%"]],
    }
    chart = slides[3]["elements"][1]
    assert chart["categories"] == ["I кв.", "II кв."]
    assert chart["series"] == ["Пары"]
    assert chart["axisTitles"] == {"category": "Квартал", "value": "Пары"}


def test_element_number_is_constrained_by_the_schema() -> None:
    """Номер элемента — перечисление в схеме: провайдер держит её при
    декодировании, и замечание не укажет на блок, которого нет."""
    slide = sample().slides[1]
    contract = look_contract(slide)

    contract.model_validate({"readability": [{"element": 2, "problem": "Шапки не видно"}], "offTopicPictures": []})
    with pytest.raises(ValidationError):
        contract.model_validate({"readability": [{"element": 3, "problem": "Шапки не видно"}], "offTopicPictures": []})
    assert contract.model_json_schema()["$defs"]["Unreadable"]["properties"]["element"]["enum"] == [1, 2]


def test_slide_without_content_has_nothing_to_be_unreadable() -> None:
    contract = look_contract(Slide(id="s9", layout_id="l1"))

    contract.model_validate(clean())
    with pytest.raises(ValidationError):
        contract.model_validate({"readability": [{"element": 1, "problem": "Мелко"}], "offTopicPictures": []})


def test_slide_failing_the_contract_stops_the_stage(tmp_path: Path) -> None:
    provider = Provider({2: ["{}", "{}"]})

    with pytest.raises(ModelError) as error:
        review_visual(sample(), pictures(tmp_path), client(provider), load_prompts())

    assert error.value.stage == VISUAL_STAGE


def test_images_must_match_the_slides(tmp_path: Path) -> None:
    provider = Provider()

    with pytest.raises(ValueError, match="изображений 2, а слайдов 3"):
        review_visual(sample(), pictures(tmp_path, count=2), client(provider), load_prompts())
    assert provider.requests == []


# --- Проверки -------------------------------------------------------------------------


def test_unreadable_block_becomes_a_finding_on_that_block(tmp_path: Path) -> None:
    problem = "Шапки таблицы не видно: текст того же цвета, что заливка"
    provider = Provider({2: [{"readability": [{"element": 2, "problem": problem}], "offTopicPictures": []}]})

    report = audit_visual(sample(), pictures(tmp_path), client(provider), load_prompts())

    [finding] = report.findings
    assert finding.check_id == READABILITY
    assert (finding.check_class, finding.severity, finding.fixability) == ("model", "advice", "lossy")
    assert (finding.slide_number, finding.slot_id) == (2, "body")
    assert problem in finding.message
    assert finding.evidence["slideId"] == "s2"


def test_picture_off_the_topic_is_a_warning(tmp_path: Path) -> None:
    picture = {"shows": "пляж с пальмами", "why": "слайд о числе пар наставников"}
    provider = Provider({3: [{"readability": [], "offTopicPictures": [picture]}]})

    report = audit_visual(sample(), pictures(tmp_path), client(provider), load_prompts())

    [finding] = report.findings
    assert finding.check_id == IMAGERY
    assert (finding.severity, finding.slide_number, finding.slot_id) == ("warning", 3, None)
    assert "пляж с пальмами" in finding.message and "слайд о числе пар наставников" in finding.message
    assert finding.evidence["slideId"] == "s3"


def test_clean_slides_give_no_findings_and_both_checks_run(tmp_path: Path) -> None:
    report = audit_visual(sample(), pictures(tmp_path), client(Provider()), load_prompts())

    assert report.findings == []
    assert {IMAGERY, READABILITY} <= set(report.checks_run)
    assert report.checks_skipped == []


def test_without_a_model_visual_checks_are_named_skipped() -> None:
    """Пустой список находок без модели неотличим от чистого слайда — поэтому
    проверки названы пропущенными, а не выполненными."""
    report = audit_visual(sample())

    assert report.findings == []
    assert {IMAGERY, READABILITY} <= set(report.checks_skipped)
    assert not {IMAGERY, READABILITY} & set(report.checks_run)


def test_model_without_images_is_a_loud_error() -> None:
    with pytest.raises(ValueError, match="изображени"):
        audit_visual(sample(), None, client(Provider()), load_prompts())


def test_visual_report_goes_after_the_variant_findings(tmp_path: Path) -> None:
    """Способы исправления адресуются номером находки в отчёте варианта —
    находки визуала дописываются в конец и номеров не сдвигают, а
    «пропущено» сменяется «выполнено»."""
    own = Finding.model_validate(
        {
            "checkId": "density.too_many_bullets",
            "category": "density",
            "class": "file",
            "severity": "warning",
            "fixability": "lossy",
            "message": "Много пунктов",
            "slideNumber": 1,
        }
    )
    variant = attach(
        AuditReport(run_id="r", variant="A", findings=[own], checks_run=["density.too_many_bullets"]),
        audit_visual(sample()),
    )
    assert {IMAGERY, READABILITY} <= set(variant.checks_skipped)

    problem = "Подписи осей сливаются с фоном"
    provider = Provider({3: [{"readability": [{"element": 2, "problem": problem}], "offTopicPictures": []}]})
    seen = attach(variant, audit_visual(sample(), pictures(tmp_path), client(provider), load_prompts()), sample())

    assert [f.check_id for f in seen.findings] == ["density.too_many_bullets", READABILITY]
    assert {IMAGERY, READABILITY} <= set(seen.checks_run)
    assert not {IMAGERY, READABILITY} & set(seen.checks_skipped)
