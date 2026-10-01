"""Подслой 4a: аудит текста колоды (T-51).

Выполняется один раз на колоду до вёрстки — не на каждый вариант: текст во
всех трёх вариантах один и тот же, а различается подача. Находки этого
класса правятся перегенерацией текста, и узнавать о них после вёрстки трёх
вариантов поздно.

**Модели — один запрос на колоду, все вопросы разом** (бюджет
`models-strategy.md`). Вопрос «связан ли слайд с соседним» без соседей не
задашь, а запрос на слайд стоил бы четырнадцати обращений вместо одного.
Ответ `TextReview` лежит в контексте аудита, и каждая проверка
`checks/content.py` читает из него свою часть — проверки остаются
самостоятельными единицами реестра.

**Без модели аудит текста не молчит, а называет пропущенное.** Смысловые
проверки уходят в `checks_skipped`, у гибридных — их смысловая половина с
пометкой `:model`: детерминированная часть выполнена, и выдать её за полную
проверку значило бы скрыть урезание (EF-9).

**Цитата в ответе модели — адрес находки.** Контракт требует, чтобы каждая
цитата была на слайде дословно: выдуманная указывала бы в никуда, а
переписанная своими словами — на текст, которого у автора нет.
"""

from __future__ import annotations

import inspect
import json
from typing import Literal

from pydantic import Field, create_model, model_validator

from dpd.audit.registry import AuditContext, discover, run_checks
from dpd.generation import ContentPack, fact_sections
from dpd.generation.content import slide_texts
from dpd.llm import ModelClient, PromptSet
from dpd.models import (
    AuditReport,
    Finding,
    PresentationStructure,
    RenderedPresentation,
    StructureSlide,
)
from dpd.models.common import Contract

PROMPT = "skills/content-validator"
TEXT_STAGE = "Проверка текста"

QUOTE_MARKS = " «»\"'“”„"


# --- Контракт ответа --------------------------------------------------------------


class Verdict(Contract):
    """Ответ «да/нет» с краткой причиной (формат чек-листа)."""

    ok: bool
    reason: str


class Typo(Contract):
    wrong: str = Field(min_length=1)
    right: str = Field(min_length=1)


class SlideReview(Contract):
    """Ответы о слайде: вопросы 1, 2, 3, 4, 7, 8, 10 и 11 Приложения 1."""

    id: str
    headline_is_conclusion: Verdict
    body_supports_headline: Verdict
    one_sentence: Verdict
    unsupported_claims: list[str]
    service_text: list[str]
    typos: list[Typo]
    off_point_items: list[str]
    follows_previous: Verdict

    def verdicts(self) -> list[tuple[str, Verdict]]:
        return [
            (field.alias or name, value)
            for name, field in type(self).model_fields.items()
            if isinstance(value := getattr(self, name), Verdict)
        ]

    def quotes(self) -> list[tuple[str, str]]:
        """Всё, что модель процитировала со слайда, с именем поля."""
        return [
            *(("unsupportedClaims", quote) for quote in self.unsupported_claims),
            *(("serviceText", quote) for quote in self.service_text),
            *(("typos.wrong", typo.wrong) for typo in self.typos),
            *(("offPointItems", quote) for quote in self.off_point_items),
        ]


class TextReview(Contract):
    """Ответ модели о колоде целиком: по одному `SlideReview` на слайд."""

    slides: list[SlideReview]

    def by_id(self) -> dict[str, SlideReview]:
        return {item.id: item for item in self.slides}


def review_contract(structure: PresentationStructure) -> type[TextReview]:
    """Контракт ответа для колоды: идентификаторы слайдов — в схеме, цитаты — в проверке."""
    ids = [slide.id for slide in structure.slides]
    texts = {slide.id: plain(" \n ".join(text for _, text in visible_texts(slide))) for slide in structure.slides}

    def check(review: TextReview) -> TextReview:
        # Правила вне схемы: их держит не провайдер, а повтор клиента.
        problems: list[str] = []
        seen = [item.id for item in review.slides]
        missing = [slide_id for slide_id in ids if slide_id not in seen]
        repeated = sorted({slide_id for slide_id in seen if seen.count(slide_id) > 1})
        if missing:
            problems.append(f"нет ответа для слайдов {', '.join(missing)} — нужен ровно один ответ на каждый слайд")
        if repeated:
            problems.append(f"слайды {', '.join(repeated)} встречаются в ответе больше одного раза")
        for item in review.slides:
            for name, verdict in item.verdicts():
                if not verdict.ok and not verdict.reason.strip():
                    problems.append(f"{item.id}.{name}: при ok = false нужна reason — причина для автора")
            for name, quote in item.quotes():
                if plain(quote) not in texts[item.id]:
                    problems.append(f"{item.id}.{name}: «{quote}» нет на слайде — нужна дословная цитата")
        if problems:
            raise ValueError("; ".join(problems))
        return review

    slide = create_model("SlideReview", __base__=SlideReview, id=(Literal[tuple(ids)], ...))  # type: ignore[valid-type]
    return create_model(
        "TextReview",
        __base__=TextReview,
        __validators__={"quoted_from_slides": model_validator(mode="after")(check)},
        slides=(list[slide], Field(min_length=len(ids), max_length=len(ids))),  # type: ignore[valid-type]
    )


def visible_texts(slide: StructureSlide) -> list[tuple[str, str]]:
    """Текст, который увидит зритель. Ключевое сообщение на слайд не выводится (T-50)."""
    return [(where, text) for where, text in slide_texts(slide) if where != "keyMessage" and text]


def plain(text: str) -> str:
    """Запись для сравнения цитаты со слайдом: регистр, «ё», пробелы и кавычки не важны."""
    return " ".join(text.strip(QUOTE_MARKS).lower().replace("ё", "е").split())


# --- Запрос к модели ----------------------------------------------------------------


def review_text(
    structure: PresentationStructure,
    pack: ContentPack,
    client: ModelClient,
    prompts: PromptSet,
) -> TextReview:
    """Ответы модели на смысловые вопросы о колоде — одним запросом.

    Модель видит бриф, фактуру по разделам с метками `[content.md#…]` — те
    же, что при генерации, — и текст слайдов JSON-ом. Невалидный после всех
    попыток ответ поднимает `ModelError` с этапом `TEXT_STAGE`.
    """
    facts = "\n\n".join(f"[{anchor}]\n{text}" for anchor, text in fact_sections(pack.content).items())
    task = {"slides": [_card(number, slide) for number, slide in enumerate(structure.slides, start=1)]}
    user = f"{pack.brief}\n\n{facts}\n\n{json.dumps(task, ensure_ascii=False)}"
    return client.complete(prompts.get(PROMPT).text, user, review_contract(structure), stage=TEXT_STAGE)


def _card(number: int, slide: StructureSlide) -> dict:
    card: dict = {
        "id": slide.id,
        "number": number,
        "role": slide.role,
        "headline": slide.headline,
        "body": list(slide.body.items) if slide.body else [],
    }
    visual = slide.visualization
    if visual and visual.table:
        card["table"] = visual.table.model_dump(by_alias=True)
    if visual and visual.chart:
        card["chart"] = visual.chart.model_dump(by_alias=True, exclude_none=True)
    card["sourceRefs"] = list(slide.source_refs)
    return card


# --- Подслой целиком ----------------------------------------------------------------


def audit_text(
    structure: PresentationStructure,
    pack: ContentPack | None = None,
    client: ModelClient | None = None,
    prompts: PromptSet | None = None,
    *,
    run_id: str | None = None,
) -> AuditReport:
    """Выполнить проверки подслоя 4a.

    С клиентом модели — все девять проверок; без него — детерминированные
    и детерминированные половины гибридных, остальное названо пропущенным.
    Без контент-пакета числа сверять не с чем, и проверка чисел пропускается:
    у плана, написанного человеком, источника нет.
    """
    review = None
    if client is not None:
        if pack is None or prompts is None:
            raise ValueError("смысловой аудит текста требует контент-пакета и промптов")
        review = review_text(structure, pack, client, prompts)

    registry = discover()
    context = AuditContext(structure=structure, content_pack=pack, review=review)
    report = run_checks(context, run_id=run_id, sublayers=("4a",), registry=registry)
    if review is not None:
        return report

    halves = [
        f"{registered.spec.id}:model"
        for registered in registry
        if registered.spec.id in report.checks_run and registered.parameters.get("review", inspect.Parameter.empty) is None
    ]
    return report.model_copy(update={"checks_skipped": [*report.checks_skipped, *halves]})


def attach(
    report: AuditReport,
    text: AuditReport | None,
    deck: RenderedPresentation | None = None,
) -> AuditReport:
    """Дополнить отчёт варианта находками аудита текста.

    Проверки текста выполняются один раз на колоду, и их результат главнее:
    прогон варианта мог выполнить их же без контент-пакета и без модели, то
    есть частично. Находки текста идут в конец — номера находок варианта, по
    которым интерфейс предлагает способы исправления, не сдвигаются.

    `deck` — свёрстанный вариант: после переноса части слайда на новый номера
    следующих слайдов сдвигаются, а находка текста привязана к слайду
    замысла (`evidence.slideId`), и номер берётся по нему.
    """
    if text is None:
        return report
    owned = {item.split(":")[0] for item in (*text.checks_run, *text.checks_skipped)}
    positions = {slide.id: number for number, slide in enumerate(deck.slides, start=1)} if deck else {}

    kept = [finding for finding in report.findings if finding.check_id not in owned]
    added = [_placed(finding, positions) for finding in text.findings]
    findings = [finding.model_copy(update={"id": f"f{number}"}) for number, finding in enumerate([*kept, *added], start=1)]
    return report.model_copy(
        update={
            "findings": findings,
            "checks_run": [item for item in report.checks_run if item not in owned] + list(text.checks_run),
            "checks_skipped": [item for item in report.checks_skipped if item.split(":")[0] not in owned]
            + list(text.checks_skipped),
        }
    )


def _placed(finding: Finding, positions: dict[str, int]) -> Finding:
    number = positions.get(str(finding.evidence.get("slideId")))
    return finding if number is None else finding.model_copy(update={"slide_number": number})


__all__ = [
    "PROMPT",
    "TEXT_STAGE",
    "SlideReview",
    "TextReview",
    "attach",
    "audit_text",
    "review_contract",
    "review_text",
    "visible_texts",
]
