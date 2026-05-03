from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

from revenue_segment_extractor.extraction.schemas import ExtractedRevenueRow, RevenueExtractionOutput


SCALE_MULTIPLIERS = {
    "actuals": Decimal("1"),
    "ones": Decimal("1"),
    "thousands": Decimal("1000"),
    "millions": Decimal("1000000"),
    "billions": Decimal("1000000000"),
}

CURRENCY_SYMBOLS = {
    "$": "USD",
    "US$": "USD",
    "USD$": "USD",
    "USD": "USD",
    "€": "EUR",
    "EUR": "EUR",
    "£": "GBP",
    "GBP": "GBP",
    "CHF": "CHF",
    "DKK": "DKK",
    "SEK": "SEK",
    "NOK": "NOK",
    "CAD": "CAD",
    "AUD": "AUD",
    "JPY": "JPY",
    "CNY": "CNY",
    "RMB": "CNY",
    "HKD": "HKD",
    "SAR": "SAR",
}

DASH_MARKERS = {"", "-", "\u2013", "\u2014", "\u2212", "--"}


class NormalizationDocument(Protocol):
    fiscal_period: str | None
    currency: str | None
    scale: str | None


@dataclass(frozen=True)
class NormalizationWarning:
    field: str
    message: str


@dataclass(frozen=True)
class NormalizedDecimal:
    value: Decimal | None
    warnings: tuple[NormalizationWarning, ...]


@dataclass(frozen=True)
class NormalizedText:
    value: str | None
    warnings: tuple[NormalizationWarning, ...]


@dataclass(frozen=True)
class NormalizedPageReference:
    value: str | None
    page_number: int | None
    warnings: tuple[NormalizationWarning, ...]


@dataclass(frozen=True)
class NormalizedRevenueRow:
    source_row: ExtractedRevenueRow
    segment_name: str
    revenue_raw: str | None
    revenue_value: Decimal | None
    currency: str | None
    scale: str | None
    period_label: str | None
    normalized_value: Decimal | None
    page_ref: str | None
    page_number: int | None
    section_ref: str | None
    metric_basis: str | None
    evidence_text: str
    extraction_confidence: float | None
    fiscal_period: str | None
    page_relevance_score: float | None
    warnings: tuple[NormalizationWarning, ...]

    def as_extracted_row(self) -> ExtractedRevenueRow:
        return self.source_row.model_copy(
            update={
                "segment_name": self.segment_name,
                "revenue_raw": self.revenue_raw,
                "revenue_value": self.revenue_value,
                "currency": self.currency,
                "scale": self.scale,
                "period_label": self.period_label,
                "page_ref": self.page_ref,
                "section_ref": self.section_ref,
                "metric_basis": self.metric_basis,
                "evidence_text": self.evidence_text,
                "confidence": self.extraction_confidence,
            }
        )


def normalize_extracted_row(
    row: ExtractedRevenueRow,
    output: RevenueExtractionOutput,
    document: NormalizationDocument,
    *,
    page_relevance_score: float | None = None,
    page_context_text: str | None = None,
) -> NormalizedRevenueRow:
    warnings: list[NormalizationWarning] = []

    revenue_result = normalize_revenue_value(row.revenue_raw, fallback_value=row.revenue_value)
    warnings.extend(revenue_result.warnings)

    currency_result = normalize_currency(
        row.currency,
        fallback=output.currency or document.currency,
        evidence_text=" ".join(
            item
            for item in [
                row.revenue_raw or "",
                row.evidence_text,
                row.metric_basis or "",
                page_context_text or "",
            ]
            if item
        ),
    )
    warnings.extend(currency_result.warnings)

    scale_result = normalize_scale(
        row.scale,
        fallback=output.scale or document.scale,
        evidence_text=" ".join(
            item
            for item in [
                row.evidence_text,
                row.metric_basis or "",
                row.section_ref or "",
                page_context_text or "",
            ]
            if item
        ),
    )
    warnings.extend(scale_result.warnings)

    period_result = normalize_period_label(row.period_label or output.fiscal_period or document.fiscal_period)
    warnings.extend(period_result.warnings)

    page_result = normalize_page_reference(row.page_ref)
    warnings.extend(page_result.warnings)

    normalized_value = normalize_scaled_value(revenue_result.value, scale_result.value)
    if revenue_result.value is not None and normalized_value is None:
        warnings.append(
            NormalizationWarning(
                field="normalized_value",
                message="Revenue value could not be scaled because scale is missing or unsupported.",
            )
        )

    source_row = row.model_copy(
        update={
            "revenue_value": revenue_result.value,
            "currency": currency_result.value,
            "scale": scale_result.value,
            "period_label": period_result.value,
            "page_ref": page_result.value,
        }
    )

    return NormalizedRevenueRow(
        source_row=source_row,
        segment_name=row.segment_name.strip(),
        revenue_raw=row.revenue_raw,
        revenue_value=revenue_result.value,
        currency=currency_result.value,
        scale=scale_result.value,
        period_label=period_result.value,
        normalized_value=normalized_value,
        page_ref=page_result.value,
        page_number=page_result.page_number,
        section_ref=_clean_optional_text(row.section_ref),
        metric_basis=_clean_optional_text(row.metric_basis),
        evidence_text=row.evidence_text.strip(),
        extraction_confidence=row.confidence,
        fiscal_period=period_result.value,
        page_relevance_score=page_relevance_score,
        warnings=tuple(warnings),
    )


def normalize_currency(
    raw_currency: str | None,
    *,
    fallback: str | None = None,
    evidence_text: str | None = None,
) -> NormalizedText:
    warnings: list[NormalizationWarning] = []
    candidates = [
        candidate
        for candidate in (
            _currency_from_text(raw_currency),
            _currency_from_text(evidence_text),
            _currency_from_text(fallback),
        )
        if candidate
    ]
    unique = tuple(dict.fromkeys(candidates))
    if not unique:
        return NormalizedText(
            value=None,
            warnings=(
                NormalizationWarning("currency", "Currency is missing or unsupported."),
            ),
        )
    if len(unique) > 1:
        warnings.append(
            NormalizationWarning(
                "currency",
                f"Multiple currency signals found: {', '.join(unique)}.",
            )
        )
    if raw_currency is None and fallback:
        warnings.append(
            NormalizationWarning("currency", "Currency was inferred from document context.")
        )
    return NormalizedText(value=unique[0], warnings=tuple(warnings))


def normalize_scale(
    raw_scale: str | None,
    *,
    fallback: str | None = None,
    evidence_text: str | None = None,
) -> NormalizedText:
    warnings: list[NormalizationWarning] = []
    candidates = [
        candidate
        for candidate in (
            _scale_from_text(raw_scale),
            _scale_from_text(evidence_text),
            _scale_from_text(fallback),
        )
        if candidate
    ]
    unique = tuple(dict.fromkeys(candidates))
    if not unique:
        return NormalizedText(
            value=None,
            warnings=(NormalizationWarning("scale", "Scale is missing or unsupported."),),
        )
    if len(unique) > 1:
        warnings.append(
            NormalizationWarning("scale", f"Multiple scale signals found: {', '.join(unique)}.")
        )
    if raw_scale is None and fallback:
        warnings.append(NormalizationWarning("scale", "Scale was inferred from document context."))
    return NormalizedText(value=unique[0], warnings=tuple(warnings))


def normalize_revenue_value(
    raw_value: str | None,
    *,
    fallback_value: Decimal | None = None,
    dash_means_zero: bool = False,
) -> NormalizedDecimal:
    if raw_value is None:
        if fallback_value is None:
            return NormalizedDecimal(
                value=None,
                warnings=(NormalizationWarning("revenue_value", "Revenue value is missing."),),
            )
        return NormalizedDecimal(
            value=fallback_value,
            warnings=(
                NormalizationWarning(
                    "revenue_value",
                    "Revenue value was taken from structured model output because raw value is missing.",
                ),
            ),
        )

    stripped = raw_value.strip()
    normalized_dash = _normalize_dash(stripped)
    if normalized_dash in DASH_MARKERS:
        value = Decimal("0") if dash_means_zero else None
        message = (
            "Dash-only revenue value was treated as zero because table context explicitly allowed it."
            if dash_means_zero
            else "Dash-only or blank revenue value is not treated as numeric zero."
        )
        return NormalizedDecimal(
            value=value,
            warnings=(NormalizationWarning("revenue_value", message),),
        )

    value = _parse_decimal_text(stripped)
    if value is not None:
        return NormalizedDecimal(value=value, warnings=())
    if fallback_value is not None:
        return NormalizedDecimal(
            value=fallback_value,
            warnings=(
                NormalizationWarning(
                    "revenue_value",
                    "Raw revenue value could not be parsed; using structured model value.",
                ),
            ),
        )
    return NormalizedDecimal(
        value=None,
        warnings=(
            NormalizationWarning(
                "revenue_value",
                f"Raw revenue value could not be parsed: {raw_value!r}.",
            ),
        ),
    )


def normalize_scaled_value(value: Decimal | None, scale: str | None) -> Decimal | None:
    if value is None:
        return None
    multiplier = SCALE_MULTIPLIERS.get((scale or "").strip().casefold())
    if multiplier is None:
        return None
    return value * multiplier


def normalize_period_label(raw_period: str | None) -> NormalizedText:
    if raw_period is None or not raw_period.strip():
        return NormalizedText(
            value=None,
            warnings=(NormalizationWarning("period_label", "Fiscal period is missing."),),
        )

    cleaned = re.sub(r"\s+", " ", raw_period.strip())
    quarter_match = re.search(r"\b(Q[1-4])\s*(?:FY)?\s*((?:19|20)\d{2})\b", cleaned, re.I)
    if quarter_match:
        return NormalizedText(
            value=f"{quarter_match.group(1).upper()} {quarter_match.group(2)}",
            warnings=(),
        )

    year = _latest_year(cleaned)
    if year is None:
        return NormalizedText(
            value=cleaned,
            warnings=(
                NormalizationWarning(
                    "period_label",
                    "Fiscal period has no detectable four-digit year.",
                ),
            ),
        )
    return NormalizedText(value=f"FY{year}", warnings=())


def normalize_page_reference(raw_page_ref: str | None) -> NormalizedPageReference:
    if raw_page_ref is None or not raw_page_ref.strip():
        return NormalizedPageReference(
            value=None,
            page_number=None,
            warnings=(NormalizationWarning("page_ref", "Page reference is missing."),),
        )

    text = raw_page_ref.strip()
    numbers = [int(match) for match in re.findall(r"\d+", text)]
    if not numbers:
        return NormalizedPageReference(
            value=None,
            page_number=None,
            warnings=(
                NormalizationWarning(
                    "page_ref",
                    f"Page reference has no page number: {raw_page_ref!r}.",
                ),
            ),
        )

    if len(numbers) >= 2 and re.search(r"\b(pp|pages?)\b|[-\u2013\u2014]", text, re.I):
        return NormalizedPageReference(
            value=f"pp. {numbers[0]}-{numbers[1]}",
            page_number=numbers[0],
            warnings=(),
        )
    return NormalizedPageReference(value=f"p. {numbers[0]}", page_number=numbers[0], warnings=())


def _currency_from_text(text: str | None) -> str | None:
    if not text:
        return None
    upper_text = text.upper()
    for token, currency in CURRENCY_SYMBOLS.items():
        if token in {"$", "€", "£"}:
            if token in text:
                return currency
            continue
        if re.search(rf"(?<![A-Z]){re.escape(token)}(?![A-Z])", upper_text):
            return currency
    if re.search(r"\bUS\s+dollars?\b|\bU\.S\.\s+dollars?\b", text, re.I):
        return "USD"
    if re.search(r"\bsaudi\s+riyals?\b|\bsaudi\s+arabian\s+riyals?\b", text, re.I):
        return "SAR"
    return None


def _scale_from_text(text: str | None) -> str | None:
    if not text:
        return None
    normalized = re.sub(r"\s+", " ", text.strip().casefold())
    if re.search(r"\b(actuals?|ones|units?)\b|\bin\s+(?:whole\s+)?(?:dollars?|euros?|pounds?)\b", normalized):
        return "actuals"
    if re.search(r"\b(thousands?|000s?|k)\b|['`]000|\$000|eur000|gbp000|usd000", normalized):
        return "thousands"
    if re.search(r"\b(millions?|million|mn)\b|(?:usd|eur|gbp|chf|dkk|sek|nok)?m\b|[$€£]m\b", normalized):
        return "millions"
    if re.search(r"\b(billions?|billion|bn)\b|(?:usd|eur|gbp|chf|dkk|sek|nok)?b\b|[$€£]b\b", normalized):
        return "billions"
    return None


def _parse_decimal_text(text: str) -> Decimal | None:
    cleaned = _normalize_dash(text)
    is_parenthesized_negative = cleaned.startswith("(") and cleaned.endswith(")")
    if is_parenthesized_negative:
        cleaned = cleaned[1:-1]

    cleaned = re.sub(
        r"(?i)\b(usd|eur|gbp|chf|dkk|sek|nok|cad|aud|jpy|cny|rmb|hkd|"
        r"millions?|billions?|thousands?|actuals?|mn|bn)\b",
        "",
        cleaned,
    )
    cleaned = cleaned.replace("$", "").replace("€", "").replace("£", "")
    cleaned = cleaned.replace("'", "").replace("\u00a0", " ")
    cleaned = cleaned.strip()

    trailing_negative = cleaned.endswith("-") and re.search(r"\d", cleaned[:-1])
    leading_negative = cleaned.startswith("-")
    cleaned = cleaned.strip("-").strip()
    numeric_text = re.sub(r"[^0-9,.\s]", "", cleaned)
    numeric_text = _normalize_number_separators(numeric_text)
    if not numeric_text:
        return None
    try:
        value = Decimal(numeric_text)
    except InvalidOperation:
        return None

    if is_parenthesized_negative or trailing_negative or leading_negative:
        return -value
    return value


def _normalize_number_separators(text: str) -> str:
    compact = re.sub(r"\s+", "", text)
    if "," in compact and "." in compact:
        comma_index = compact.rfind(",")
        dot_index = compact.rfind(".")
        decimal_separator = "," if comma_index > dot_index else "."
        thousands_separator = "." if decimal_separator == "," else ","
        compact = compact.replace(thousands_separator, "")
        if decimal_separator == ",":
            compact = compact.replace(",", ".")
        return compact
    if "," in compact:
        if re.search(r",\d{1,2}$", compact) and not re.search(r",\d{3}(?:,|$)", compact):
            return compact.replace(",", ".")
        return compact.replace(",", "")
    return compact


def _normalize_dash(text: str) -> str:
    return text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")


def _latest_year(text: str) -> int | None:
    years = [int(match) for match in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", text)]
    return max(years) if years else None


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value.strip())
    return cleaned or None
