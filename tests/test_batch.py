"""T-42: девять колод промежуточной сдачи одной командой.

Матрица — три калибровочных шаблона на три варианта вёрстки, на одном
контенте (`docs/08-backlog/deliverables.md`). Имена файлов заданы схемой
сдачи: `{слаг шаблона}__{вариант}`, колоды в `decks/`, отчёты аудита в
`audit/`. По имени комиссия находит колоду, не открывая её, и схема
проверяется здесь буквально.

Основная команда гоняется один раз на модуль и без PDF: конвертация
LibreOffice — 16 с на шаблон, а имена от формата не зависят. PDF проверяется
отдельно, на одном шаблоне.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from dpd import batch
from dpd.batch import deck_name, load_matrix, main
from dpd.llm import ModelClient, ModelSettings, PromptSet, build_client
from dpd.render import soffice_path

OUTLINE = """Итоги пилота
Что изменилось за квартал
- Срок сборки колоды сократился с девяти дней до трёх
- Правки текста переживают сохранение файла
Как шли
- Разбор шаблона
- Вёрстка
- Аудит
"""

PACK = Path(__file__).resolve().parents[1] / "assets" / "content-pack"
SETTINGS = ModelSettings(
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

SLUGS = ("vk-tech", "vk-workspace", "vk-education")
NINE = [f"{slug}__{variant}" for slug in SLUGS for variant in "abc"]


def _base() -> Path:
    return Path(os.environ.get("DPD_OUTPUT_DIR", "D:/dpd-out")) / "tests" / "batch"


def _fresh(name: str) -> Path:
    path = _base() / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


@pytest.fixture(scope="module")
def outline() -> Path:
    path = _base() / "outline.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(OUTLINE, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def built(outline: Path) -> Iterator[tuple[int, Path]]:
    for _, template in load_matrix():
        if not template.exists():
            pytest.skip(f"нет калибровочного шаблона {template.name}")
    out = _fresh("nine")
    code = main(["--outline", str(outline), "--out", str(out), "--formats", "pptx,html"])
    yield code, out


# --- Матрица и имена -------------------------------------------------------


def test_matrix_names_the_three_calibration_templates() -> None:
    matrix = load_matrix()
    assert [slug for slug, _ in matrix] == list(SLUGS)
    assert all(template.suffix == ".pptx" for _, template in matrix)


ACCEPTANCE_MATRIX = batch.ROOT / "configs" / "acceptance.yaml"
ACCEPTANCE_DIR = batch.ROOT / "assets" / "templates" / "acceptance"


def test_acceptance_run_names_the_external_template_only() -> None:
    """T-43: прогон на приёмочном шаблоне — тот же батч с другим перечнем.

    Шаблон в тестах не разбирается: настроенный на нём тест перестал бы
    быть доказательством универсальности (правило 7 CLAUDE.md).
    """
    matrix = load_matrix(ACCEPTANCE_MATRIX)
    assert [slug for slug, _ in matrix] == ["external"]
    template = matrix[0][1]
    assert template.parent == ACCEPTANCE_DIR
    assert template.is_file()


def test_acceptance_template_stays_out_of_the_nine_decks() -> None:
    assert all(template.parent != ACCEPTANCE_DIR for _, template in load_matrix())


DEBUG_DIR = batch.ROOT / "assets" / "templates" / "debug"
DEBUGGED = "Jessica · SlidesCarnival.pptx"
TEMPLATE_SUFFIXES = (".pptx", ".potx")


def test_acceptance_template_is_not_the_debugged_one() -> None:
    """T-59: шаблон, на котором отлаживали, приёмку не доказывает.

    Jessica разобрана в T-57 (решение по T20) и переехала в `debug/`; в
    `acceptance/` остаётся только тот шаблон, на который указывает перечень.
    """
    (_, template), = load_matrix(ACCEPTANCE_MATRIX)
    assert template.name != DEBUGGED
    assert (DEBUG_DIR / DEBUGGED).is_file()
    found = [path for path in ACCEPTANCE_DIR.iterdir() if path.suffix in TEMPLATE_SUFFIXES]
    assert found == [template]


@pytest.mark.parametrize("folder", [ACCEPTANCE_DIR, DEBUG_DIR], ids=["acceptance", "debug"])
def test_every_kept_template_has_a_license_card(folder: Path) -> None:
    """Шаблон без карточки не используется (README приёмочного каталога).

    Лицензия CC BY требует атрибуции там, где шаблон лежит, — в том числе
    после переезда в другой каталог.
    """
    readme = (folder / "README.md").read_text(encoding="utf-8")
    templates = [path for path in folder.iterdir() if path.suffix in TEMPLATE_SUFFIXES]
    assert templates, f"в {folder.name}/ нет шаблона"
    for template in templates:
        card = readme.split(f"Файл:            {template.name}", 1)
        assert len(card) == 2, f"нет карточки для {template.name}"
        for field in ("Источник:", "Правообладатель:", "Лицензия:"):
            assert field in card[1].split("```", 1)[0], f"в карточке {template.name} нет «{field}»"


def test_deck_name_follows_the_deliverables_scheme() -> None:
    assert deck_name("vk-tech", "A") == "vk-tech__a"
    assert deck_name("vk-education", "c") == "vk-education__c"


# --- Одна команда — девять колод -------------------------------------------


def test_one_command_produces_nine_decks(built: tuple[int, Path]) -> None:
    code, out = built
    assert code == 0
    for extension in ("pptx", "html"):
        names = sorted(path.stem for path in (out / "decks").glob(f"*.{extension}"))
        assert names == sorted(NINE), extension


def test_every_deck_has_its_audit_report(built: tuple[int, Path]) -> None:
    _, out = built
    reports = sorted((out / "audit").glob("*.report.json"))
    assert [path.name.removesuffix(".report.json") for path in reports] == sorted(NINE)
    for path in reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        # Отчёт принадлежит своей колоде, а не соседней: вариант в имени
        # файла и внутри отчёта совпадает.
        assert path.name.split("__")[1][0] == report["variant"].lower()
        assert report["timings"], path.name
        # План, написанный человеком, модель не смотрела: смысловые проверки
        # текста названы пропущенными, а не выданы за чистый текст (T-51).
        assert "content.headline_no_conclusion" in report["checksSkipped"], path.name
        assert "content.mixed_language" in report["checksRun"], path.name


def test_nothing_but_the_scheme_is_left_behind(built: tuple[int, Path]) -> None:
    _, out = built
    # Рабочие файлы прогона — промежуточные PDF, имена по файлу шаблона — в
    # каталог сдачи не попадают: комиссия увидела бы в нём лишнее.
    assert sorted(path.name for path in out.iterdir()) == ["audit", "decks"]


# --- Форматы ---------------------------------------------------------------


def test_every_deck_goes_out_in_three_formats(outline: Path) -> None:
    if soffice_path() is None:
        pytest.skip("LibreOffice не найден")
    matrix = dict(load_matrix())
    if not matrix["vk-tech"].exists():
        pytest.skip("нет калибровочного шаблона VK Tech")
    config = _base() / "one.yaml"
    config.write_text(
        f"version: 1\ntemplates:\n  - slug: vk-tech\n    file: {matrix['vk-tech'].as_posix()}\n",
        encoding="utf-8",
    )
    out = _fresh("formats")

    code = main(["--outline", str(outline), "--out", str(out), "--matrix", str(config)])

    assert code == 0
    files = sorted(path.name for path in (out / "decks").iterdir())
    assert files == sorted(
        f"vk-tech__{variant}.{extension}" for variant in "abc" for extension in ("pptx", "pdf", "html")
    )


# --- Отказы ----------------------------------------------------------------


def test_broken_template_does_not_stop_the_others(outline: Path) -> None:
    matrix = dict(load_matrix())
    if not matrix["vk-tech"].exists():
        pytest.skip("нет калибровочного шаблона VK Tech")
    broken = _base() / "broken.pptx"
    broken.write_bytes(b"not a presentation")
    config = _base() / "broken.yaml"
    config.write_text(
        "version: 1\ntemplates:\n"
        f"  - slug: broken\n    file: {broken.as_posix()}\n"
        f"  - slug: vk-tech\n    file: {matrix['vk-tech'].as_posix()}\n",
        encoding="utf-8",
    )
    out = _fresh("broken")

    code = main(["--outline", str(outline), "--out", str(out), "--matrix", str(config), "--formats", "html"])

    # Сбой одного шаблона виден по коду возврата, но остальные колоды
    # собраны: девять колод не должны зависеть от самого слабого файла.
    assert code != 0
    assert sorted(path.stem for path in (out / "decks").iterdir()) == ["vk-tech__a", "vk-tech__b", "vk-tech__c"]


def test_console_encoding_cannot_fail_the_batch(outline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    matrix = dict(load_matrix())
    if not matrix["vk-tech"].exists():
        pytest.skip("нет калибровочного шаблона VK Tech")
    config = _base() / "one.yaml"
    config.write_text(
        f"version: 1\ntemplates:\n  - slug: vk-tech\n    file: {matrix['vk-tech'].as_posix()}\n",
        encoding="utf-8",
    )
    # Найдено живым прогоном: вывод в канал на Windows идёт в cp1251, и
    # символ вне её ронял команду уже после того, как все колоды собраны.
    # Сводка в канале — это журнал, его читают как UTF-8.
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1251"))

    code = main(["--outline", str(outline), "--out", str(_fresh("encoding")), "--matrix", str(config), "--formats", "html"])

    sys.stdout.flush()
    assert code == 0
    assert "Итого" in raw.getvalue().decode("utf-8")


def test_unknown_format_is_refused(outline: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--outline", str(outline), "--out", str(tmp_path), "--formats", "pptx,docx"])


def test_outline_and_content_pack_together_are_refused(outline: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--outline", str(outline), "--content-pack", str(PACK), "--out", str(tmp_path)])


def test_content_pack_without_its_files_is_refused_before_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_requests(prompts: object) -> ModelClient:
        raise AssertionError("запрос к модели до проверки входа")

    monkeypatch.setattr(batch, "model_client", no_requests)
    with pytest.raises(SystemExit):
        main(["--content-pack", str(tmp_path), "--out", str(tmp_path / "out")])


# --- Колоды из контент-пакета (T-50) -----------------------------------------


GENERATED_BODY = "Наставник рядом с первого дня работы"


def fake_model(request: httpx.Request) -> httpx.Response:
    """Модель-подделка: структура колоды и содержание каждого слайда.

    Ответы проходят проверку чисел на настоящей фактуре: в них нет ни одного
    числа, а ссылка ведёт на настоящий раздел `assets/content-pack/content.md`.
    """
    body = json.loads(request.content)
    if body["response_format"]["json_schema"]["name"] == "SlideLook":
        content = {"lines": [], "offTopicPictures": []}
    elif body["response_format"]["json_schema"]["name"] == "TextReview":
        job = json.loads(body["messages"][1]["content"].rsplit("\n\n", 1)[1])
        ok = {"ok": True, "reason": ""}
        verdicts = ("bodySupportsHeadline", "oneSentence", "followsPrevious")
        lists = ("unsupportedClaims", "serviceText", "typos", "offPointItems")
        content = {
            "slides": [
                {"id": item["id"], "headlineKind": "claim", **dict.fromkeys(verdicts, ok), **{name: [] for name in lists}}
                for item in job["slides"]
            ]
        }
    elif body["response_format"]["json_schema"]["name"] == "DeckOutline":
        roles = ["title", "agenda", *["data"] * 7, "closing"]
        content = {
            "meta": {"purpose": "initiative", "audience": "руководители", "language": "ru"},
            "slides": [{"role": role, "headline": "Тема", "keyMessage": "Мысль"} for role in roles],
        }
    else:
        job = json.loads(body["messages"][1]["content"].rsplit("\n\n", 1)[1])
        title = job["slide"]["role"] == "title"
        content = {
            "headline": "Наставничество окупилось" if not title else "Программа «Навигатор»",
            "keyMessage": "Пилот готов к масштабированию",
            "body": None if title else {"kind": "bullets", "items": [GENERATED_BODY]},
            "visualization": None,
            "sourceRefs": [] if title else ["content.md#суть-программы"],
        }
    message = {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)}
    return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}]})


def test_decks_are_built_from_the_content_pack_without_a_human_outline(monkeypatch: pytest.MonkeyPatch) -> None:
    matrix = dict(load_matrix())
    if not matrix["vk-tech"].exists():
        pytest.skip("нет калибровочного шаблона VK Tech")
    config = _base() / "one.yaml"
    config.write_text(
        f"version: 1\ntemplates:\n  - slug: vk-tech\n    file: {matrix['vk-tech'].as_posix()}\n",
        encoding="utf-8",
    )
    calls: list[int] = []

    def mocked(prompts: PromptSet) -> ModelClient:
        calls.append(1)
        return build_client(SETTINGS, prompts, transport=httpx.MockTransport(fake_model))

    monkeypatch.setattr(batch, "model_client", mocked)
    looked: list[int] = []

    def vision(prompts: PromptSet) -> ModelClient:
        looked.append(1)
        return build_client(SETTINGS, prompts, transport=httpx.MockTransport(fake_model))

    monkeypatch.setattr(batch, "vision_client", vision)
    fake_render(monkeypatch)
    out = _fresh("pack")

    # Контент-пакет — вход по умолчанию: плана, написанного человеком, нет.
    code = main(["--out", str(out), "--matrix", str(config), "--formats", "html"])

    assert code == 0
    assert calls == [1]
    deck = (out / "decks" / "vk-tech__a.html").read_text(encoding="utf-8")
    assert GENERATED_BODY in deck
    # Аудит текста выполнен один раз до вёрстки и вошёл в отчёт колоды (T-51).
    report = json.loads((out / "audit" / "vk-tech__a.report.json").read_text(encoding="utf-8"))
    assert "content.headline_no_conclusion" in report["checksRun"]
    # Колоды сдачи — все три варианта, выбора нет: VLM смотрит каждую (T-52).
    assert looked == [1]
    for variant in ("a", "b", "c"):
        report = json.loads((out / "audit" / f"vk-tech__{variant}.report.json").read_text(encoding="utf-8"))
        assert {"content.irrelevant_imagery", "content.visual_readability"} <= set(report["checksRun"])
        assert not [item for item in report["checksSkipped"] if item.startswith("content.")]


def fake_render(monkeypatch: pytest.MonkeyPatch) -> None:
    """LibreOffice подменён: превью по одному на слайд колоды."""
    from pptx import Presentation

    from dpd import orchestrator

    def render(pdf, out, **_):
        Path(out).mkdir(parents=True, exist_ok=True)
        count = len(Presentation(str(Path(pdf).with_suffix(".pptx"))).slides)
        images = [Path(out) / f"slide-{number:03d}.png" for number in range(1, count + 1)]
        for image in images:
            image.write_bytes(b"png")
        return images

    monkeypatch.setattr(
        orchestrator, "convert_to_pdfs", lambda paths, out, **_: [Path(path).with_suffix(".pdf") for path in paths]
    )
    monkeypatch.setattr(orchestrator, "render_pdf_pages", render)
