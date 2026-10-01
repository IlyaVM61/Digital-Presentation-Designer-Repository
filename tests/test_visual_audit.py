"""T-52: аудит визуала, подслой 4c.

Две проверки, которым нужно изображение слайда: вопрос 6 Приложения 1 —
картинки и пиктограммы по теме слайда (`content.irrelevant_imagery`), — и
визуальная читаемость сверх набора ТЗ (`content.visual_readability`).
Остальные вопросы валидации решаются по тексту (T-51) и по данным файла.

**Один запрос на слайд, все вопросы разом** (критерий приёмки).

**Модель не судит о читаемости, а переписывает текст, который видит**, и
помечает строки, разбираемые с трудом. Со списком того, что положила
вёрстка, расшифровку сравнивает код: строки нет в расшифровке — её не видно.
Так решено по двум живым прогонам: модель, которую просили судить, на
светлом шаблоне пропускала шапку таблицы цвета заливки, а на тёмном
выдумывала перекрытый текст там, где всё читается. Списка модель не видит —
иначе переписала бы его, а не изображение.

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
from dpd.audit.checks.visual import check_visual_readability
from dpd.audit.textual import attach
from dpd.audit.visual import (
    VISUAL_STAGE,
    SlideLook,
    VisualReview,
    audit_visual,
    review_visual,
)
from dpd.llm import ModelClient, ModelError, ModelSettings, load_prompts
from dpd.models import AuditReport, Finding, RenderedPresentation
from dpd.models.common import Bounds, Canvas, TextRun
from dpd.models.rendered import RenderedChart, RenderedElement, RenderedTable, Slide
from dpd.models.structure import AxisTitles, ChartSeries

READABILITY = "content.visual_readability"
IMAGERY = "content.irrelevant_imagery"

BOX = Bounds(x=0.1, y=0.1, w=0.8, h=0.3)

SEEN = {
    1: ["Наставничество окупилось", "vk tech"],
    2: ["Удержание выросло", "Показатель", "До", "После", "Удержание", "78%", "89%"],
    3: ["Пар стало вдвое больше", "Пары", "100", "50", "0", "I кв.", "II кв.", "Квартал"],
}
"""Что модель прочитала бы на чистых слайдах `sample()`; «vk tech» — надпись шаблона."""


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


def seen(*lines: str, faint: tuple[str, ...] = (), pictures: list[dict] | None = None) -> dict:
    """Ответ модели: прочитанные строки и картинки не по теме."""
    return {
        "lines": [{"text": line, "legibility": "faint" if line in faint else "clear"} for line in lines],
        "offTopicPictures": pictures or [],
    }


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


def number_of(body: dict) -> int:
    [part] = [item for item in body["messages"][1]["content"] if item["type"] == "text"]
    return json.loads(part["text"])["number"]


class Provider:
    """Поддельный VLM: отвечает по номеру слайда из запроса.

    Слайды смотрятся параллельно, и порядок запросов не определён — ответ
    выбирается по слайду, а не по очереди. Без заданного ответа модель
    читает слайд целиком (`SEEN`).
    """

    def __init__(self, answers: dict[int, list[dict | str]] | None = None) -> None:
        self.answers = answers or {}
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        with self.lock:
            self.requests.append(body)
            number = number_of(body)
            queue = self.answers.get(number)
            answer = queue.pop(0) if queue else seen(*SEEN[number])
        content = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        message = {"role": "assistant", "content": content}
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}]})

    def by_slide(self) -> dict[int, dict]:
        return {number_of(body): body for body in self.requests}


def client(provider: Provider) -> ModelClient:
    prompts = load_prompts()
    return ModelClient(settings(), repair_prompt=prompts.get("skills/response-repair").text, transport=httpx.MockTransport(provider))


def readability(deck: RenderedPresentation, *answers: dict, share: float = 0.5) -> list[Finding]:
    review = VisualReview(slides=[SlideLook.model_validate(answer) for answer in answers])
    return check_visual_readability(deck, review, min_seen_share=share)


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


def test_model_is_not_shown_the_slide_text(tmp_path: Path) -> None:
    """Расшифровка должна быть расшифровкой изображения: со списком строк
    перед глазами модель переписала бы список, и невидимый текст «нашёлся» бы."""
    provider = Provider()

    review_visual(sample(), pictures(tmp_path), client(provider), load_prompts())

    for body in provider.requests:
        sent = json.dumps(body["messages"], ensure_ascii=False)
        assert "Показатель" not in sent and "Удержание выросло" not in sent


def test_legibility_is_one_of_two_marks() -> None:
    SlideLook.model_validate(seen("Заголовок", faint=("Заголовок",)))
    with pytest.raises(ValidationError):
        SlideLook.model_validate({"lines": [{"text": "Заголовок", "legibility": "hidden"}], "offTopicPictures": []})


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


# --- Читаемость ------------------------------------------------------------------------


def test_text_missing_from_the_image_is_a_warning_on_its_block() -> None:
    """Шапка таблицы цвета заливки: на изображении её нет — содержимое
    потеряно для зрителя, это тяжелее, чем «читается с трудом»."""
    deck = sample()
    findings = readability(deck, seen(*SEEN[1]), seen("Удержание выросло", "Удержание", "78%", "89%"), seen(*SEEN[3]))

    [finding] = findings
    assert finding.check_id == READABILITY
    assert (finding.check_class, finding.severity, finding.fixability) == ("model", "warning", "lossy")
    assert (finding.slide_number, finding.slot_id) == (2, "body")
    assert "«Показатель», «До», «После»" in finding.message
    assert finding.evidence == {"slideId": "s2", "element": 2, "missing": ["Показатель", "До", "После"]}


def test_text_read_with_strain_is_advice() -> None:
    deck = sample()
    findings = readability(deck, seen(*SEEN[1]), seen(*SEEN[2]), seen(*SEEN[3], faint=("Пары", "Квартал")))

    [finding] = findings
    assert (finding.severity, finding.slide_number, finding.slot_id) == ("advice", 3, "body")
    assert "«Пары», «Квартал»" in finding.message
    assert finding.evidence["faint"] == ["Пары", "Квартал"]


def test_text_wrapped_over_lines_counts_as_seen() -> None:
    """Абзац на изображении переносится, и модель пишет его по строкам."""
    deck = sample()

    assert readability(deck, seen("Наставничество", "окупилось"), seen(*SEEN[2]), seen(*SEEN[3])) == []


def test_small_reading_slips_do_not_make_text_unseen() -> None:
    """Расшифровка неточна — «е» вместо «ё», потерянная кавычка, слово
    с ошибкой. Строка считается увиденной, если прочитана хотя бы доля слов
    из `configs/audit.yaml`."""
    deck = sample()
    slipped = seen("Пар стало вдвое бальше", *SEEN[3][1:])

    assert readability(deck, seen(*SEEN[1]), seen(*SEEN[2]), slipped) == []
    assert readability(deck, seen(*SEEN[1]), seen(*SEEN[2]), slipped, share=1.0) != []


def test_template_text_is_not_ours_to_check() -> None:
    """Надпись шаблона в расшифровке не проверяется: проверка ищет то, что
    сделали мы (правило 12)."""
    deck = sample()

    assert readability(deck, seen(*SEEN[1], faint=("vk tech",)), seen(*SEEN[2]), seen(*SEEN[3])) == []


# --- Картинки и подслой целиком --------------------------------------------------------


def test_picture_off_the_topic_is_a_warning(tmp_path: Path) -> None:
    picture = {"shows": "пляж с пальмами", "why": "слайд о числе пар наставников"}
    provider = Provider({3: [seen(*SEEN[3], pictures=[picture])]})

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

    provider = Provider({3: [seen(*SEEN[3], faint=("Квартал",))]})
    looked = attach(variant, audit_visual(sample(), pictures(tmp_path), client(provider), load_prompts()), sample())

    assert [f.check_id for f in looked.findings] == ["density.too_many_bullets", READABILITY]
    assert {IMAGERY, READABILITY} <= set(looked.checks_run)
    assert not {IMAGERY, READABILITY} & set(looked.checks_skipped)
