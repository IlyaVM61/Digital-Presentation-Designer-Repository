"""Клиент моделей и загрузка промптов.

Клиент абстрагирован от провайдера: принимает системный промпт, запрос и
схему ответа, возвращает валидный объект. Провайдер и модель задаются
переменными окружения и `configs/models.yaml`, поэтому смена провайдера не
затрагивает код.

Промпты загружаются из файлов в `prompts/` — в коде их нет ни в каком виде.
Это прямое требование ТЗ (п. 2.4); версия набора попадает в `AuditReport`
каждого прогона, иначе утверждение о воспроизводимости непроверяемо.
"""

from dpd.llm.client import ModelClient, ModelError, build_client
from dpd.llm.prompts import Prompt, PromptError, PromptSet, load_prompts
from dpd.llm.settings import ModelSettings, load_settings

__all__ = [
    "ModelClient",
    "ModelError",
    "ModelSettings",
    "Prompt",
    "PromptError",
    "PromptSet",
    "build_client",
    "load_prompts",
    "load_settings",
]
