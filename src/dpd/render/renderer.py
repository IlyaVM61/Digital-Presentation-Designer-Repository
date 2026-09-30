"""Рендер колоды в изображения: LibreOffice в PDF, затем `pymupdf` в PNG.

Второй шаг понадобился потому, что `pdftoppm` в системе отсутствует, а тянуть
ради него Poppler незачем: `pymupdf` ставится обычным пакетом.

**Узкое место — конвертация LibreOffice.** Измерено на колоде из 29 слайдов:
28 секунд против 0,11 с на рендер страницы. Она плохо параллелится: процесс
один на колоду, а не задача на слайд. Часть времени уходит на холодный старт.

Рендер нужен в четырёх местах: аудит визуала через VLM, превью в интерфейсе,
экспорт в PDF и проверки класса `rendered`.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pymupdf

DEFAULT_DPI = 110
DEFAULT_TIMEOUT_SEC = 180

_CANDIDATES = (
    # Машина разработки: LibreOffice вынесен на диск с местом.
    "D:/LibreOffice/program/soffice.com",
    "C:/Program Files/LibreOffice/program/soffice.com",
    "C:/Program Files (x86)/LibreOffice/program/soffice.com",
    # macOS — платформа, которую ТЗ требует поддерживать.
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
)


def soffice_path() -> Path | None:
    """Найти LibreOffice: переменная окружения, затем PATH, затем обычные места.

    Возвращает `None`, если не найден: вызывающий решает, это отказ или повод
    пропустить работу. Жёсткий путь одной машины здесь недопустим — проект
    должны воспроизвести на другой.
    """
    configured = os.environ.get("SOFFICE_PATH")
    if configured and Path(configured).is_file():
        return Path(configured)

    for name in ("soffice.com", "soffice"):
        found = shutil.which(name)
        if found:
            return Path(found)

    return next((Path(c) for c in _CANDIDATES if Path(c).is_file()), None)


def convert_to_pdf(
    pptx_path: str | Path,
    out_dir: str | Path,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> Path:
    """Конвертировать `.pptx` в PDF силами LibreOffice."""
    return convert_to_pdfs([pptx_path], out_dir, timeout_sec=timeout_sec)[0]


def convert_to_pdfs(
    pptx_paths: Sequence[str | Path],
    out_dir: str | Path,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> list[Path]:
    """Конвертировать несколько `.pptx` в PDF одним запуском LibreOffice.

    Холодный старт платится один раз: измерено на трёх вариантах колоды —
    16,6 с одним процессом против 8,8 с на одну колоду, то есть около 26 с
    тремя отдельными запусками. PDF возвращаются в порядке колод.

    PDF называется по имени колоды, поэтому одноимённые колоды отвергаются до
    запуска: иначе одна молча перезаписала бы другую.
    """
    paths, out_dir = [Path(path) for path in pptx_paths], Path(out_dir)
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"файл не найден: {path}")
    stems = [path.stem for path in paths]
    repeated = sorted({stem for stem in stems if stems.count(stem) > 1})
    if repeated:
        raise ValueError(f"одноимённые колоды перезапишут друг друга: {', '.join(repeated)}")

    soffice = soffice_path()
    if soffice is None:
        raise RuntimeError(
            "LibreOffice не найден. Задайте SOFFICE_PATH — см. README, «Переменные окружения»."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    profile = out_dir / ".libreoffice-profile"
    profile.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            str(soffice),
            "--headless",
            "--norestore",
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            *(str(path) for path in paths),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )

    pdfs = [out_dir / f"{path.stem}.pdf" for path in paths]
    missing = [pdf.stem for pdf in pdfs if not pdf.is_file()]
    if missing:
        raise RuntimeError(
            f"LibreOffice не создал PDF для {', '.join(missing)} (код {result.returncode}).\n"
            f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
        )
    return pdfs


def render_pdf_pages(
    pdf_path: str | Path,
    out_dir: str | Path,
    dpi: int = DEFAULT_DPI,
) -> list[Path]:
    """Отрендерить страницы PDF в PNG.

    Разрешение задаётся в dpi, а не в пикселях: холсты шаблонов различаются,
    и фиксированная ширина дала бы разный масштаб на разных шаблонах.
    """
    pdf_path, out_dir = Path(pdf_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    images: list[Path] = []
    with pymupdf.open(str(pdf_path)) as document:
        for number, page in enumerate(document, start=1):
            image_path = out_dir / f"slide-{number:03d}.png"
            page.get_pixmap(dpi=dpi).save(str(image_path))
            images.append(image_path)
    return images


def render_slides(
    pptx_path: str | Path,
    out_dir: str | Path,
    dpi: int = DEFAULT_DPI,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> list[Path]:
    """Полный путь рендера: `.pptx` → PDF → PNG по одному на слайд."""
    out_dir = Path(out_dir)
    pdf_path = convert_to_pdf(pptx_path, out_dir, timeout_sec=timeout_sec)
    return render_pdf_pages(pdf_path, out_dir, dpi=dpi)
