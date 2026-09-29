"""Три варианта вёрстки по профилям визуального регистра.

Требование ТЗ о трёх визуально различимых вариантах — один из семи пунктов,
не подлежащих упрощению. Ось различий выбрана решением D2 и обоснована в
ADR-0004.

**Профили лежат в `configs/variants.yaml`, а не в коде.** Пороги и профили —
данные: их правка не должна требовать изменений в исходниках, и на защите
это позволяет показать, что настройка не зашита.

**Регистр опирается на три признака сразу** — предпочитаемую цветовую схему,
предпочитаемые типы макетов и сдвиг кегля по шкале шаблона. Одного признака
мало: в шаблоне может не быть цветовых вариаций, и варианты, различающиеся
только схемой, оказались бы неразличимы. Три признака дают различие даже
когда шаблон не предоставляет одного из них.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import Field

from dpd.layout.composer import compose
from dpd.models import (
    Contract,
    PresentationStructure,
    RenderedPresentation,
    TemplateSchema,
)

CONFIG_ENV = "DPD_VARIANTS_CONFIG"
DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "configs" / "variants.yaml"


class VariantProfile(Contract):
    """Визуальный регистр одного варианта."""

    id: str
    name: str
    prefer_scheme: str = "any"
    prefer_families: list[str] = Field(default_factory=list)
    size_shift: int = 0
    layout_offset: int = 0
    bullet_style: str = "compact"


def config_path() -> Path:
    configured = os.environ.get(CONFIG_ENV)
    return Path(configured) if configured else DEFAULT_CONFIG


def load_profiles() -> list[VariantProfile]:
    """Прочитать профили вариантов из конфигурации."""
    raw = yaml.safe_load(config_path().read_text(encoding="utf-8"))
    return [VariantProfile.model_validate(item) for item in raw.get("profiles", [])]


def compose_variants(
    structure: PresentationStructure,
    template: TemplateSchema,
    profiles: list[VariantProfile] | None = None,
) -> list[RenderedPresentation]:
    """Собрать колоду в трёх визуальных регистрах на одном содержании.

    Содержание не меняется — ТЗ требует три варианта на одном контенте.
    Меняется форма: какой макет выбран, в какой цветовой схеме и каким
    кеглем набран текст.
    """
    return [
        compose(
            structure,
            _tuned(template, profile),
            variant=profile.id,
            size_shift=profile.size_shift,
            layout_offset=profile.layout_offset,
        )
        for profile in (profiles or load_profiles())
    ]


def _tuned(template: TemplateSchema, profile: VariantProfile) -> TemplateSchema:
    """Подготовить схему под регистр: порядок макетов и сдвиг шкалы.

    Вёрстка выбирает первый подходящий макет, поэтому регистр выражается
    перестановкой: предпочитаемые типы и схема поднимаются вверх. Это не
    обход детерминированности — порядок задаётся конфигурацией и одинаков
    при каждом прогоне.
    """
    ordered = sorted(
        template.layouts,
        key=lambda layout: (
            _family_rank(layout.family, profile),
            _scheme_rank(layout.color_scheme, profile),
        ),
    )

    return template.model_copy(update={"layouts": ordered})


def _family_rank(family: str, profile: VariantProfile) -> int:
    return profile.prefer_families.index(family) if family in profile.prefer_families else 99


def _scheme_rank(scheme: str, profile: VariantProfile) -> int:
    if profile.prefer_scheme == "any":
        return 0
    return 0 if scheme == profile.prefer_scheme else 1
