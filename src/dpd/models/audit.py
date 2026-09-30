"""Находка аудита и отчёт прогона.

Модель находки задана в `docs/04-architecture/audit-architecture.md`. Каждое
поле отвечает на вопрос, без которого находку нельзя ни показать, ни
исправить:

- `category` и `check_class` — раздел Приложения 1 и источник данных. Класс
  указывается явно, потому что находку по рендеру нельзя выдавать за
  полностью детерминированную: она зависит от окружения рендера;
- `fixability` — политика D5, то есть поведение фиксера;
- `confidence` — уверенность токенов, на которые опиралась проверка. Правило,
  выведенное предположительно, не должно предъявляться как нарушение;
- `environment` — сведения об окружении для класса `rendered`: при отсутствии
  гарнитур шаблона метрики текста меняются, и находка это оговаривает.

Ключи JSON совпадают с нормативным примером архитектуры, включая `class`:
контракт описан там, а не выводится из имён Python.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from dpd.models.common import Contract

Severity = Literal["critical", "warning", "advice"]

Fixability = Literal["none", "mechanical", "lossy", "semantic"]
"""Политика D5: механическое исправляется автоматически, с потерями —
спрашивает пользователя, смысловое предлагает перегенерацию слайда,
`none` — не исправляется вовсе."""

Category = Literal["layout", "template", "density", "integrity", "content", "variants"]
"""Разделы Приложения 1 плюс `variants` — расширение сверх набора ТЗ."""

CheckClass = Literal["file", "rendered", "model"]
"""Источник данных и, следовательно, тип проверки по ТЗ: `file` и `rendered`
детерминированные, `model` — нет."""

FindingStatus = Literal["open", "autofixed", "ignored", "deferred"]

SEVERITY_ORDER: tuple[Severity, ...] = ("critical", "warning", "advice")
"""Порядок показа: критичное нельзя не заметить."""


class Finding(Contract):
    """Обнаруженный дефект."""

    check_id: str
    category: Category
    check_class: CheckClass = Field(alias="class")
    severity: Severity
    fixability: Fixability
    message: str
    slide_number: int | None = None
    slot_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0, le=1)
    status: FindingStatus = "open"
    environment: dict[str, Any] | None = None
    id: str | None = None


class AuditReport(Contract):
    """Результат аудита одного варианта колоды.

    `checks_run` и `checks_skipped` обязательны по той же причине, по которой
    у находки есть класс: пустой список находок иначе неотличим от «проверки
    не выполнялись». Проверка пропускается, когда её вход отсутствует —
    например, файловые проверки целостности до экспорта.

    `prompt_versions` фиксирует, какими промптами получен результат: без
    этого утверждение о воспроизводимости недетерминированной части
    непроверяемо (требование п. 2.4 ТЗ).

    `timings` — секунды по этапам прогона целиком, а не одного варианта
    (NFR-1а, T-41): разбор и конвертация общие для трёх вариантов, и у их
    отчётов разбивка одна и та же.
    """

    run_id: str
    variant: str | None = None
    findings: list[Finding] = Field(default_factory=list)
    checks_run: list[str] = Field(default_factory=list)
    checks_skipped: list[str] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)
    prompt_versions: dict[str, str] = Field(default_factory=dict)

    def by_severity(self, severity: Severity) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == severity]
