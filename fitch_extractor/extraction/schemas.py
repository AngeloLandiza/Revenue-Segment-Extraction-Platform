from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictExtractionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


EsgFactorType = Literal[
    "emissions_target",
    "decarbonization_plan",
    "renewable_investment",
    "fossil_fuel_exposure",
    "coal_phaseout",
    "controversy",
    "regulatory_violation",
    "safety_incident",
    "social_program",
    "labor_issue",
    "circular_economy",
    "biodiversity_impact",
    "water_risk",
    "governance_policy",
    "company_wide_policy",
    "other",
]

EsgPolarity = Literal["positive", "negative", "neutral", "mixed", "unknown"]
EsgSegmentLinkType = Literal[
    "direct_segment_name",
    "asset_or_project",
    "activity_type",
    "geography",
    "company_wide",
    "unclear",
]
EsgCategory = Literal["E", "S", "G", "unknown"]


class ExtractedRevenueRow(StrictExtractionModel):
    segment_name: str
    revenue_raw: str | None
    revenue_value: Decimal | None
    currency: str | None
    scale: str | None
    period_label: str | None
    page_ref: str | None
    section_ref: str | None
    metric_basis: str | None
    evidence_text: str
    confidence: float | None = Field(ge=0, le=1)
    extraction_notes: str | None

    @field_validator("segment_name", "evidence_text")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


class RevenueExtractionOutput(StrictExtractionModel):
    company_name: str | None
    document_name: str | None
    fiscal_period: str | None
    reported_total: Decimal | None
    currency: str | None
    scale: str | None
    rows: list[ExtractedRevenueRow]
    extraction_warnings: list[str]


class CandidateDiscoveryPage(StrictExtractionModel):
    page_number: int = Field(ge=1)
    reason: str
    confidence: float | None = Field(ge=0, le=1)

    @field_validator("reason")
    @classmethod
    def require_non_empty_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


class CandidateDiscoveryOutput(StrictExtractionModel):
    selected_pages: list[CandidateDiscoveryPage]
    extraction_warnings: list[str]


class VerificationRowReference(StrictExtractionModel):
    segment_name: str
    page_ref: str | None
    confidence: float | None = Field(ge=0, le=1)
    rationale: str

    @field_validator("segment_name", "rationale")
    @classmethod
    def require_non_empty_reference_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


class VerificationSuspectedError(StrictExtractionModel):
    segment_name: str
    issue_type: str
    suggested_action: str
    rationale: str

    @field_validator("segment_name", "issue_type", "suggested_action", "rationale")
    @classmethod
    def require_non_empty_error_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


class VerificationMissingRow(StrictExtractionModel):
    segment_name: str
    revenue_raw: str | None
    currency: str | None
    scale: str | None
    period_label: str | None
    page_ref: str | None
    evidence_text: str
    rationale: str

    @field_validator("segment_name", "evidence_text", "rationale")
    @classmethod
    def require_non_empty_missing_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


class VerificationCorrectionSuggestion(StrictExtractionModel):
    segment_name: str
    field_name: str
    current_value: str | None
    suggested_value: str | None
    rationale: str

    @field_validator("segment_name", "field_name", "rationale")
    @classmethod
    def require_non_empty_correction_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


class RevenueVerificationOutput(StrictExtractionModel):
    confirmed_rows: list[VerificationRowReference]
    suspected_errors: list[VerificationSuspectedError]
    missing_rows: list[VerificationMissingRow]
    correction_suggestions: list[VerificationCorrectionSuggestion]
    rationale: str

    @field_validator("rationale")
    @classmethod
    def require_non_empty_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


class RevenueArbitrationOutput(StrictExtractionModel):
    accepted_rows: list[VerificationRowReference]
    rejected_rows: list[VerificationSuspectedError]
    missing_rows: list[VerificationMissingRow]
    correction_suggestions: list[VerificationCorrectionSuggestion]
    requires_human_review: bool
    rationale: str

    @field_validator("rationale")
    @classmethod
    def require_non_empty_arbitration_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


class ExtractedEsgFactor(StrictExtractionModel):
    factor_type: EsgFactorType
    polarity: EsgPolarity
    description: str
    page_ref: str | None
    evidence_text: str
    confidence: float | None = Field(ge=0, le=1)
    is_company_wide: bool
    segment_name: str | None
    linked_business_activity: str | None
    linkage_rationale: str
    segment_link_type: EsgSegmentLinkType | None = None
    esg_category: EsgCategory | None = None
    score_relevant: bool | None = None
    impact_mechanism: str | None = None

    @field_validator("description", "evidence_text", "linkage_rationale")
    @classmethod
    def require_non_empty_esg_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()

    @field_validator("segment_name", "linked_business_activity", "impact_mechanism")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class EsgExtractionOutput(StrictExtractionModel):
    factors: list[ExtractedEsgFactor]
    extraction_warnings: list[str]
