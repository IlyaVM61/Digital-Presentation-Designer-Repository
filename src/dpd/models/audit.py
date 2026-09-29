"""Находка аудита.

**Минимальный срез задачи T-10.** Полная модель находки и `AuditReport`
появятся с каркасом проверок (T-27): там добавятся категория, класс
проверки, координаты для подсветки на рендере, сведения об окружении и
пониженная уверенность. Здесь — то, без чего нельзя сообщить о дефекте.
"""

from __future__ import annotations

from typing import Literal

from dpd.models.common import Contract

Severity = Literal["critical", "warning", "advice"]

Fixability = Literal["none", "mechanical", "lossy", "semantic"]
"""Политика D5: механическое исправляется автоматически, с потерями —
спрашивает пользователя, смысловое предлагает перегенерацию слайда,
`none` — не исправляется вовсе."""


class Finding(Contract):
    """Обнаруженный дефект."""

    check_id: str
    severity: Severity
    fixability: Fixability
    message: str
    slide_number: int | None = None
