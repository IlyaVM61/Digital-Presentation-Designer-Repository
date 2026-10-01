"""Валидация контента: подслой 4a, аудит текста до вёрстки (T-51).

Вопросы 1–4 и 7–11 раздела «Валидация контента» Приложения 1. Пятый
(«есть ли на слайде содержание») проверяет `integrity.empty_slide` по
свёрстанной колоде, шестой (картинки и иконки) смотрит на изображение и
относится к аудиту визуала (T-52).

**Смысловые вопросы читают ответ модели, а не задают его.** Модели задаётся
один запрос на колоду (`dpd.audit.textual`), и ответ лежит в контексте
аудита как `review`. Без него смысловая проверка пропускается — и это видно
в отчёте.

**Гибридные проверки — числа и служебный мусор — начинают с поиска.**
Число, которого нет в материалах, и маркер служебного текста находятся без
модели, класс находки `file`; модель добавляет то, что поиску не видно, —
утверждения без чисел и мусор без маркеров, класс находки `model`. Архитектура
аудита требует называть класс явно: находку модели нельзя выдавать за
воспроизводимую.

**Проверяется то, что увидит зритель.** Ключевое сообщение на слайд не
выводится (решение T-50), и числа или опечатки в нём не предъявляются.

Все находки — `semantic`: их исправляет перегенерация слайда, а не правка
свойства (D5). Каждая несёт `evidence.slideId` — по нему отчёт варианта
находит слайд, даже когда номера сдвинулись.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING

from dpd.audit.registry import CheckSpec, check, param
from dpd.audit.textual import plain, visible_texts
from dpd.generation import ContentPack, fact_sections
from dpd.generation.content import NUMBER, canonical, fact_numbers
from dpd.generation.content_pack import BRIEF_FILE
from dpd.models import Finding, PresentationStructure, StructureSlide

if TYPE_CHECKING:
    from dpd.audit.textual import SlideReview, TextReview, Verdict


def _spec(check_id: str, check_class: str, severity: str, title: str) -> CheckSpec:
    return CheckSpec(
        id=check_id,
        category="content",
        check_class=check_class,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        fixability="semantic",
        sublayer="4a",
        title=title,
    )


HEADLINE_NO_CONCLUSION = _spec(
    "content.headline_no_conclusion", "model", "advice", "Заголовок называет тему, а не вывод"
)
BODY_MISMATCH_HEADLINE = _spec(
    "content.body_mismatch_headline", "model", "warning", "Содержимое слайда не соответствует заголовку"
)
NOT_SUMMARIZABLE = _spec(
    "content.not_summarizable", "model", "advice", "Слайд не пересказывается одним предложением"
)
UNSOURCED_NUMBERS = _spec(
    "content.unsourced_numbers", "file", "critical", "Цифры или факты слайда не найдены в исходных материалах"
)
SERVICE_GARBAGE = _spec(
    "content.service_garbage", "file", "critical", "На слайде служебный текст: реплики докладчику, куски задания"
)
TYPOS = _spec("content.typos", "model", "warning", "В тексте опечатки")
MIXED_LANGUAGE = _spec("content.mixed_language", "file", "warning", "Часть текста не на языке презентации")
TABLE_ROWS_IRRELEVANT = _spec(
    "content.table_rows_irrelevant", "model", "advice", "Строки таблицы или элементы диаграммы не работают на мысль слайда"
)
SLIDES_DISCONNECTED = _spec(
    "content.slides_disconnected", "model", "advice", "Слайд не связан по логике с предыдущим"
)

SERVICE_ROLES = frozenset({"title", "cover", "agenda", "section", "divider", "closing"})
"""Роли, чей заголовок служебный: вывода от титульного или плана не ждут."""


# --- Смысловые вопросы ----------------------------------------------------------


@check(HEADLINE_NO_CONCLUSION)
def check_headline_no_conclusion(structure: PresentationStructure, review: TextReview) -> list[Finding]:
    """Вопрос 1: заголовок содержит вывод, а не называет тему.

    Модель называет вид заголовка, текст замечания пишет программа. Служебные
    роли исключаются здесь, а не только в промпте: правило детерминированное,
    и полагаться в нём на модель незачем.
    """
    return [
        HEADLINE_NO_CONCLUSION.finding(
            f"Заголовок «{slide.headline}» называет тему, а не вывод: из него не видно, что доказывает слайд.",
            slide_number=number,
            evidence={"slideId": slide.id, "headline": slide.headline},
        )
        for number, slide, answer in _answered(structure, review)
        if answer.headline_kind == "topic" and slide.role not in SERVICE_ROLES
    ]


@check(BODY_MISMATCH_HEADLINE)
def check_body_mismatch_headline(structure: PresentationStructure, review: TextReview) -> list[Finding]:
    """Вопрос 2: содержимое слайда доказывает заголовок, а не говорит о другом."""
    return _verdicts(
        structure,
        review,
        BODY_MISMATCH_HEADLINE,
        lambda answer: answer.body_supports_headline,
        "Текст слайда не подтверждает заголовок",
        skip=lambda slide, _: not _has_content(slide),
    )


@check(NOT_SUMMARIZABLE)
def check_not_summarizable(structure: PresentationStructure, review: TextReview) -> list[Finding]:
    """Вопрос 3: главная мысль слайда пересказывается одним предложением."""
    return _verdicts(
        structure,
        review,
        NOT_SUMMARIZABLE,
        lambda answer: answer.one_sentence,
        "У слайда нет одной главной мысли",
        skip=lambda slide, _: not _has_content(slide),
    )


@check(SLIDES_DISCONNECTED)
def check_slides_disconnected(structure: PresentationStructure, review: TextReview) -> list[Finding]:
    """Вопрос 11: слайд продолжает мысль предыдущего. Первому продолжать нечего."""
    return _verdicts(
        structure,
        review,
        SLIDES_DISCONNECTED,
        lambda answer: answer.follows_previous,
        "Слайд не продолжает мысль предыдущего",
        skip=lambda _, number: number == 1,
    )


@check(TYPOS)
def check_typos(structure: PresentationStructure, review: TextReview) -> list[Finding]:
    """Вопрос 8: текст без опечаток. Находка называет исправление.

    «Опечатка» с цифрами отбрасывается: живой прогон показал, что модель так
    «исправляет» выдуманное число на число из соседней строки фактуры. Числа
    сверяет `content.unsourced_numbers` — по материалам и воспроизводимо.
    """
    return [
        TYPOS.finding(
            f"Опечатка: «{typo.wrong}» — вероятно, «{typo.right}».",
            slide_number=number,
            evidence={"slideId": slide.id, "wrong": typo.wrong, "right": typo.right},
        )
        for number, slide, answer in _answered(structure, review)
        for typo in answer.typos
        if not any(char.isdigit() for char in typo.wrong + typo.right)
    ]


@check(TABLE_ROWS_IRRELEVANT)
def check_table_rows_irrelevant(structure: PresentationStructure, review: TextReview) -> list[Finding]:
    """Вопрос 10: строки таблицы и элементы легенды работают на мысль слайда."""
    return [
        TABLE_ROWS_IRRELEVANT.finding(
            f"Без «{item}» вывод слайда не ослабнет — возможно, это лишнее.",
            slide_number=number,
            evidence={"slideId": slide.id, "item": item},
        )
        for number, slide, answer in _answered(structure, review)
        if slide.visualization is not None
        for item in answer.off_point_items
    ]


# --- Гибридные проверки ---------------------------------------------------------


@check(UNSOURCED_NUMBERS)
def check_unsourced_numbers(
    structure: PresentationStructure,
    content_pack: ContentPack,
    review: TextReview | None = None,
) -> list[Finding]:
    """Вопрос 4: все цифры и факты со слайда есть в исходных материалах.

    Числа сверяются с разделами, на которые слайд ссылается (`sourceRefs`):
    совпадение с числом из другого раздела бывает случайным, и такая
    находка — предупреждение, а не нарушение: то ли это число, решает
    человек. Числа, которого нет в материалах вовсе, — критично. У слайда без
    ссылок — у плана, написанного человеком, — источник весь контент-пакет.

    Записи числа сравниваются так же, как в контракте генерации (T-50):
    «1 240» и «1240», «2,8 млн» и 2800000 — одно число.

    Убранное генерацией со слайда (`omitted`, T-61) — предупреждение: на
    слайде выдуманного числа нет, но пункт или визуализация пропали, и
    вписать ли их, проверив число, решает человек.
    """
    numbers = {anchor: fact_numbers(text) for anchor, text in fact_sections(content_pack.content).items()}
    numbers[BRIEF_FILE] = fact_numbers(content_pack.brief)
    everywhere = set().union(*numbers.values())

    findings: list[Finding] = []
    for number, slide in enumerate(structure.slides, start=1):
        refs = [anchor for anchor in slide.source_refs if anchor in numbers]
        cited = set().union(*(numbers[anchor] for anchor in refs)) if slide.source_refs else everywhere
        reported: set[str] = set()
        for where, text in _visible(slide):
            for raw in NUMBER.findall(text):
                value = canonical(raw)
                if value in cited or value in reported:
                    continue
                reported.add(value)
                holders = [anchor for anchor, found in numbers.items() if value in found]
                evidence = {"slideId": slide.id, "field": where, "number": value, "text": text, "sections": holders}
                if holders:
                    findings.append(
                        UNSOURCED_NUMBERS.finding(
                            f"Число {raw} есть в материалах, но не там, на что опирается слайд, — "
                            f"проверьте, то ли это число: «{text}».",
                            severity="warning",
                            slide_number=number,
                            evidence=evidence,
                        )
                    )
                else:
                    findings.append(
                        UNSOURCED_NUMBERS.finding(
                            f"Числа {raw} нет в исходных материалах: «{text}».",
                            slide_number=number,
                            evidence=evidence,
                        )
                    )

    dropped = [
        UNSOURCED_NUMBERS.finding(
            f"Со слайда убрано «{omission.text}»: "
            f"{'числа' if len(omission.numbers) == 1 else 'чисел'} {', '.join(omission.numbers)} "
            "нет в исходных материалах. Проверьте по материалам и при необходимости впишите сами.",
            severity="warning",
            slide_number=number,
            evidence={"slideId": slide.id, "omitted": omission.text, "numbers": omission.numbers},
        )
        for number, slide in enumerate(structure.slides, start=1)
        for omission in slide.omitted
    ]

    if review is not None:
        # Строка, уже отмеченная сверкой чисел, второй раз не предъявляется:
        # остаётся воспроизводимая находка. Модель повторяла её как
        # «утверждение», хотя числа ей проверять не велено.
        flagged = {(finding.slide_number, plain(finding.evidence["text"])) for finding in findings}
        findings += [
            UNSOURCED_NUMBERS.finding(
                f"Утверждения нет в исходных материалах: «{claim}».",
                check_class="model",
                slide_number=number,
                evidence={"slideId": slide.id, "quote": claim},
            )
            for number, slide, answer in _answered(structure, review)
            for claim in answer.unsupported_claims
            if not any(at == number and plain(claim) in text for at, text in flagged)
        ]
    return findings + dropped


@check(SERVICE_GARBAGE)
def check_service_garbage(
    structure: PresentationStructure,
    markers: list[str] | None = None,
    endings: list[str] | None = None,
    review: TextReview | None = None,
) -> list[Finding]:
    """Вопрос 7: нет служебного мусора — реплик докладчику, кусков промпта.

    Поиском находятся маркеры из конфигурации, обрывы текста и фигурные
    скобки: в деловом тексте их не бывает, а в незаполненном шаблоне
    подстановки — всегда. Остальное — мусор без маркеров — находит модель.
    """
    markers = markers if markers is not None else param(SERVICE_GARBAGE.id, "markers")
    endings = endings if endings is not None else param(SERVICE_GARBAGE.id, "endings")
    lowered = [str(marker).lower() for marker in markers]

    findings: list[Finding] = []
    for number, slide in enumerate(structure.slides, start=1):
        for where, text in _visible(slide):
            cause = _garbage(text, lowered, endings)
            if cause is None:
                continue
            findings.append(
                SERVICE_GARBAGE.finding(
                    f"Похоже на служебный текст ({cause}): «{text}».",
                    slide_number=number,
                    evidence={"slideId": slide.id, "field": where, "text": text, "cause": cause},
                )
            )

    if review is not None:
        findings += [
            SERVICE_GARBAGE.finding(
                f"Похоже на служебный текст: «{fragment}».",
                check_class="model",
                slide_number=number,
                evidence={"slideId": slide.id, "quote": fragment},
            )
            for number, slide, answer in _answered(structure, review)
            for fragment in answer.service_text
        ]
    return findings


def _garbage(text: str, markers: list[str], endings: list[str]) -> str | None:
    lowered = text.lower()
    marker = next((item for item in markers if item in lowered), None)
    if marker is not None:
        return f"«{marker}»"
    if "{" in text or "}" in text:
        return "фигурные скобки шаблона"
    if any(text.rstrip().endswith(ending) for ending in endings):
        return "текст обрывается"
    return None


# --- Язык ------------------------------------------------------------------------


@check(MIXED_LANGUAGE)
def check_mixed_language(
    structure: PresentationStructure,
    min_words: int | None = None,
    max_share: float | None = None,
) -> list[Finding]:
    """Вопрос 9: вся колода на одном языке.

    Язык колоды — алфавит большинства её слов; фраза на другом — та, где
    слов другого алфавита не меньше `min_words` и их доля больше
    `max_share`. Порог по числу слов нужен из-за названий: «VK WorkSpace» или
    «LibreOffice» внутри русской фразы — не другой язык.

    Детерминированный алгоритм вместо модели — решение архитектуры аудита:
    определять алфавит по тексту модели незачем, и ошибиться она может.
    """
    min_words = int(min_words if min_words is not None else param(MIXED_LANGUAGE.id, "min_words"))
    max_share = float(max_share if max_share is not None else param(MIXED_LANGUAGE.id, "max_share"))

    units = [
        (number, slide, where, text)
        for number, slide in enumerate(structure.slides, start=1)
        for where, text in _visible(slide)
    ]
    scripts = Counter(script for *_, text in units for script in map(_script, _words(text)) if script)
    if not scripts:
        return []
    language = scripts.most_common(1)[0][0]

    findings: list[Finding] = []
    for number, slide, where, text in units:
        words = _words(text)
        foreign = [word for word in words if _script(word) not in (language, None)]
        if not foreign or len(foreign) < min_words or len(foreign) / len(words) <= max_share:
            continue
        findings.append(
            MIXED_LANGUAGE.finding(
                f"Фраза не на языке остальной презентации: «{text}».",
                slide_number=number,
                evidence={"slideId": slide.id, "field": where, "text": text, "expected": language},
            )
        )
    return findings


def _words(text: str) -> list[str]:
    return "".join(char if char.isalpha() else " " for char in text).split()


def _script(word: str) -> str | None:
    """Алфавит слова: первое слово имени символа Юникода — LATIN, CYRILLIC…"""
    names = Counter(unicodedata.name(char, "").split(" ")[0] for char in word if char.isalpha())
    return names.most_common(1)[0][0] if names else None


# --- Общее -----------------------------------------------------------------------


def _visible(slide: StructureSlide) -> list[tuple[str, str]]:
    return visible_texts(slide)


def _has_content(slide: StructureSlide) -> bool:
    return bool(slide.body and slide.body.items) or slide.visualization is not None


def _answered(structure: PresentationStructure, review: TextReview) -> list[tuple[int, StructureSlide, SlideReview]]:
    answers = review.by_id()
    return [
        (number, slide, answers[slide.id])
        for number, slide in enumerate(structure.slides, start=1)
        if slide.id in answers
    ]


def _verdicts(
    structure: PresentationStructure,
    review: TextReview,
    spec: CheckSpec,
    pick: Callable[[SlideReview], Verdict],
    lead: str,
    *,
    skip: Callable[[StructureSlide, int], bool],
) -> list[Finding]:
    """Находки по вопросу «да/нет»: слайды с ответом «нет» и причиной модели."""
    findings: list[Finding] = []
    for number, slide, answer in _answered(structure, review):
        verdict = pick(answer)
        if verdict.ok or skip(slide, number):
            continue
        findings.append(
            spec.finding(
                f"{lead}: {verdict.reason.strip()}",
                slide_number=number,
                evidence={"slideId": slide.id, "reason": verdict.reason.strip()},
            )
        )
    return findings


__all__ = [
    "check_body_mismatch_headline",
    "check_headline_no_conclusion",
    "check_mixed_language",
    "check_not_summarizable",
    "check_service_garbage",
    "check_slides_disconnected",
    "check_table_rows_irrelevant",
    "check_typos",
    "check_unsourced_numbers",
]
