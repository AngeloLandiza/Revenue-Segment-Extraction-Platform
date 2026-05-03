from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import fitz


DEFAULT_ENABLE_PAGE_TEXT_FALLBACK = False
DEFAULT_PAGE_TEXT_FALLBACK_PROVIDER = "disabled"
DEFAULT_OCR_COMMAND = "tesseract"
DEFAULT_OCR_LANGUAGES = "eng"

FallbackTextCallable = Callable[[Path, int], str | None]


@dataclass(frozen=True)
class PageTextFallbackResult:
    text: str
    parser_source: str
    provider_name: str
    warnings: tuple[str, ...] = ()


class PageTextFallbackProvider(Protocol):
    name: str
    parser_source: str

    def extract_text(self, pdf_path: Path, page_number: int) -> PageTextFallbackResult | None:
        ...


class PageTextFallbackError(RuntimeError):
    pass


@dataclass(frozen=True)
class PageTextFallbackSettings:
    enabled: bool = DEFAULT_ENABLE_PAGE_TEXT_FALLBACK
    provider_name: str = DEFAULT_PAGE_TEXT_FALLBACK_PROVIDER
    ocr_command: str = DEFAULT_OCR_COMMAND
    ocr_languages: str = DEFAULT_OCR_LANGUAGES

    @classmethod
    def from_env(cls) -> "PageTextFallbackSettings":
        return cls(
            enabled=_bool_from_env(
                "RSE_ENABLE_PAGE_TEXT_FALLBACK",
                DEFAULT_ENABLE_PAGE_TEXT_FALLBACK,
            ),
            provider_name=os.getenv(
                "RSE_PAGE_TEXT_FALLBACK_PROVIDER",
                DEFAULT_PAGE_TEXT_FALLBACK_PROVIDER,
            ),
            ocr_command=os.getenv("RSE_OCR_COMMAND", DEFAULT_OCR_COMMAND),
            ocr_languages=os.getenv("RSE_OCR_LANGUAGES", DEFAULT_OCR_LANGUAGES),
        )


class CallablePageTextFallbackProvider:
    def __init__(
        self,
        fallback: FallbackTextCallable,
        *,
        name: str = "callable_text_fallback",
        parser_source: str = "text_fallback",
    ) -> None:
        self._fallback = fallback
        self.name = name
        self.parser_source = parser_source

    def extract_text(self, pdf_path: Path, page_number: int) -> PageTextFallbackResult | None:
        text = self._fallback(pdf_path, page_number)
        if text is None:
            return None
        return PageTextFallbackResult(
            text=text,
            parser_source=self.parser_source,
            provider_name=self.name,
        )


class CallableVisionTextFallbackProvider(CallablePageTextFallbackProvider):
    def __init__(
        self,
        fallback: FallbackTextCallable,
        *,
        name: str = "callable_vision_fallback",
    ) -> None:
        super().__init__(fallback, name=name, parser_source="vision")


class FakePageTextFallbackProvider:
    name = "fake_page_text_fallback"

    def __init__(
        self,
        text_by_page: dict[int, str],
        *,
        parser_source: str = "ocr",
    ) -> None:
        self._text_by_page = dict(text_by_page)
        self.parser_source = parser_source
        self.calls: list[tuple[Path, int]] = []

    def extract_text(self, pdf_path: Path, page_number: int) -> PageTextFallbackResult | None:
        self.calls.append((pdf_path, page_number))
        if page_number not in self._text_by_page:
            return None
        return PageTextFallbackResult(
            text=self._text_by_page[page_number],
            parser_source=self.parser_source,
            provider_name=self.name,
        )


class TesseractCliOcrProvider:
    name = "tesseract_cli"
    parser_source = "ocr"

    def __init__(
        self,
        *,
        command: str = DEFAULT_OCR_COMMAND,
        languages: str = DEFAULT_OCR_LANGUAGES,
        zoom: float = 2.0,
    ) -> None:
        self.command = command
        self.languages = languages
        self.zoom = zoom

    def extract_text(self, pdf_path: Path, page_number: int) -> PageTextFallbackResult | None:
        if shutil.which(self.command) is None:
            raise PageTextFallbackError(f"OCR command not found: {self.command}")
        if page_number < 1:
            raise PageTextFallbackError("page_number must be 1-based")

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / f"page-{page_number}.png"
            _render_page(pdf_path, page_number, image_path, zoom=self.zoom)
            command = [self.command, str(image_path), "stdout", "-l", self.languages]
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            message = f"OCR command failed with exit code {completed.returncode}"
            if stderr:
                message = f"{message}: {stderr}"
            raise PageTextFallbackError(message)

        return PageTextFallbackResult(
            text=completed.stdout,
            parser_source=self.parser_source,
            provider_name=self.name,
        )


def create_page_text_fallback_provider(
    settings: PageTextFallbackSettings | None = None,
) -> PageTextFallbackProvider | None:
    resolved_settings = settings or PageTextFallbackSettings.from_env()
    if not resolved_settings.enabled:
        return None

    provider_name = resolved_settings.provider_name.strip().lower()
    if provider_name in {"", "disabled", "none"}:
        return None
    if provider_name in {"ocr", "tesseract", "tesseract_cli"}:
        return TesseractCliOcrProvider(
            command=resolved_settings.ocr_command,
            languages=resolved_settings.ocr_languages,
        )

    raise ValueError(f"Unsupported page text fallback provider: {resolved_settings.provider_name}")


def _render_page(pdf_path: Path, page_number: int, output_path: Path, *, zoom: float) -> None:
    with fitz.open(pdf_path) as document:
        if page_number > document.page_count:
            raise PageTextFallbackError(
                f"page_number {page_number} exceeds page count {document.page_count}"
            )
        page = document[page_number - 1]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pixmap.save(output_path)


def _bool_from_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")
