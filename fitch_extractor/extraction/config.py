from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_EXTRACTION_PROVIDER = "anthropic"
DEFAULT_EXTRACTION_MODEL = "claude-sonnet-4-6"
DEFAULT_ARBITRATION_MODEL = DEFAULT_EXTRACTION_MODEL
DEFAULT_PAGE_BUNDLE_SIZE = 2
DEFAULT_MAX_TOKENS = 16000
DEFAULT_TEMPERATURE = 0.0
DEFAULT_ENABLE_SECOND_PASS_VERIFICATION = True
DEFAULT_ENABLE_ARBITRATION = False


@dataclass(frozen=True)
class ExtractionSettings:
    provider_name: str = DEFAULT_EXTRACTION_PROVIDER
    model: str = DEFAULT_EXTRACTION_MODEL
    verification_model: str = DEFAULT_EXTRACTION_MODEL
    arbitration_model: str = DEFAULT_ARBITRATION_MODEL
    page_bundle_size: int = DEFAULT_PAGE_BUNDLE_SIZE
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = DEFAULT_TEMPERATURE
    enable_second_pass_verification: bool = DEFAULT_ENABLE_SECOND_PASS_VERIFICATION
    enable_arbitration: bool = DEFAULT_ENABLE_ARBITRATION

    @classmethod
    def from_env(cls) -> "ExtractionSettings":
        return cls(
            provider_name=os.getenv("FITCH_EXTRACTION_PROVIDER", DEFAULT_EXTRACTION_PROVIDER),
            model=os.getenv("FITCH_EXTRACTION_MODEL", DEFAULT_EXTRACTION_MODEL),
            verification_model=os.getenv(
                "FITCH_VERIFICATION_MODEL",
                os.getenv("FITCH_EXTRACTION_MODEL", DEFAULT_EXTRACTION_MODEL),
            ),
            arbitration_model=os.getenv(
                "FITCH_ARBITRATION_MODEL",
                os.getenv("FITCH_EXTRACTION_MODEL", DEFAULT_ARBITRATION_MODEL),
            ),
            page_bundle_size=_int_from_env(
                "FITCH_EXTRACTION_PAGE_BUNDLE_SIZE",
                DEFAULT_PAGE_BUNDLE_SIZE,
            ),
            max_tokens=_int_from_env("FITCH_EXTRACTION_MAX_TOKENS", DEFAULT_MAX_TOKENS),
            temperature=_float_from_env("FITCH_EXTRACTION_TEMPERATURE", DEFAULT_TEMPERATURE),
            enable_second_pass_verification=_bool_from_env(
                "FITCH_ENABLE_SECOND_PASS_VERIFICATION",
                DEFAULT_ENABLE_SECOND_PASS_VERIFICATION,
            ),
            enable_arbitration=_bool_from_env(
                "FITCH_ENABLE_ARBITRATION",
                DEFAULT_ENABLE_ARBITRATION,
            ),
        )


def _int_from_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


def _float_from_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


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
