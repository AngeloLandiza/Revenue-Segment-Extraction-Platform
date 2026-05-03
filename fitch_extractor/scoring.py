from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from fitch_extractor.enrichment import (
    ROW_TYPE_BUSINESS_SEGMENT,
    classify_segment_row,
    esg_category_for_factor,
    esg_cluster_key,
)
from fitch_extractor.models import (
    SEGMENT_STATUS_APPROVED,
    SEGMENT_STATUS_EDITED,
    CompanyScore,
    Document,
    EsgFactor,
    NaceCandidate,
    NaceSelection,
    ReviewEvent,
    SegmentRow,
    SegmentScore,
)
from fitch_extractor.nace.reference import NaceNode, load_nace_nodes
from fitch_extractor.persistence.repository import SQLiteRepository


DEFAULT_SCORING_RULES_PATH = Path("config/scoring_rules.yaml")
REVIEWED_SCORING_STATUSES = {SEGMENT_STATUS_APPROVED, SEGMENT_STATUS_EDITED}
TOTAL_ROW_TERMS = {
    "total",
    "total revenue",
    "total revenues",
    "total net revenue",
    "net revenue total",
    "consolidated total",
    "group total",
    "company total",
    "revenue total",
}
MAX_POSITIVE_ESG_ADJUSTMENT = -0.75
MAX_NEGATIVE_ESG_ADJUSTMENT = 1.00
SEVERE_NEGATIVE_FACTOR_TYPES = {
    "controversy",
    "regulatory_violation",
    "safety_incident",
}


@dataclass(frozen=True)
class ScoringRules:
    model_label: str
    scale_min: float
    scale_max: float
    score_direction: str
    scale_description: str
    default_base_score: float
    base_scores_by_code: dict[str, float]
    base_scores_by_division: dict[str, float]
    base_scores_by_section: dict[str, float]
    polarity_adjustments: dict[str, float]
    factor_type_adjustments: dict[str, float]
    approved_esg_statuses: set[str]


@dataclass(frozen=True)
class BaseScoreLookup:
    score: float
    rationale: str
    nace_code: str | None
    nace_label: str | None


@dataclass(frozen=True)
class SegmentScoreInput:
    row: SegmentRow
    nace_selection: NaceSelection | None
    nace_candidates: tuple[NaceCandidate, ...]
    esg_factors: tuple[EsgFactor, ...]
    weight_share: float | None


@dataclass(frozen=True)
class ScoreComputationResult:
    segment_scores: tuple[SegmentScore, ...]
    company_score: CompanyScore | None
    denominator_value: Decimal | None
    denominator_source: str
    excluded_segment_ids: tuple[str, ...]


class ScoringService:
    def __init__(
        self,
        repository: SQLiteRepository,
        rules_path: str | Path = DEFAULT_SCORING_RULES_PATH,
        nace_nodes: tuple[NaceNode, ...] | None = None,
    ) -> None:
        self.repository = repository
        self.rules = load_scoring_rules(rules_path)
        self._nace_by_code = {
            node.code: node for node in (nace_nodes if nace_nodes is not None else load_nace_nodes())
        }

    def score_document(self, document_id: str) -> ScoreComputationResult:
        document = self.repository.get_document(document_id)
        if document is None:
            raise KeyError(f"Document not found: {document_id}")

        rows = self.repository.list_segment_rows(document_id)
        review_events = self.repository.list_review_events(document_id)
        esg_factors = self.repository.list_esg_factors(document_id)
        esg_statuses = _latest_esg_statuses(review_events)
        denominator, denominator_source = self._revenue_denominator(document, rows)
        reviewed_rows = [
            row
            for row in rows
            if row.status in REVIEWED_SCORING_STATUSES and is_scorable_business_row(row)
        ]

        scores: list[SegmentScore] = []
        for row in reviewed_rows:
            weight_share = _weight_share(row.normalized_value, denominator)
            segment_input = SegmentScoreInput(
                row=row,
                nace_selection=self.repository.get_nace_selection(row.id),
                nace_candidates=tuple(self.repository.list_nace_candidates(row.id)),
                esg_factors=tuple(
                    factor
                    for factor in esg_factors
                    if factor.segment_id == row.id
                    and esg_statuses.get(factor.id) in self.rules.approved_esg_statuses
                    and factor.score_relevant is not False
                    and not factor.is_company_wide
                ),
                weight_share=weight_share,
            )
            scores.append(self.score_segment(segment_input, denominator_source))

        company_score = self._company_score(document, scores, denominator, denominator_source)
        self.repository.replace_document_scores(
            document_id,
            segment_scores=scores,
            company_score=company_score,
        )
        excluded_ids = tuple(
            row.id
            for row in rows
            if row.status not in REVIEWED_SCORING_STATUSES or not is_scorable_business_row(row)
        )
        return ScoreComputationResult(
            segment_scores=tuple(scores),
            company_score=company_score,
            denominator_value=denominator,
            denominator_source=denominator_source,
            excluded_segment_ids=excluded_ids,
        )

    def score_segment(
        self,
        segment_input: SegmentScoreInput,
        denominator_source: str = "not_calculated",
    ) -> SegmentScore:
        base = self.base_score_for_segment(
            segment_input.nace_selection,
            segment_input.nace_candidates,
        )
        adjustments, adjustment_rationale = self.esg_adjustment(segment_input.esg_factors)
        final_score = clamp_score(
            base.score + adjustments,
            self.rules.scale_min,
            self.rules.scale_max,
        )
        rationale = {
            "model_label": self.rules.model_label,
            "scale": {
                "min": self.rules.scale_min,
                "max": self.rules.scale_max,
                "direction": self.rules.score_direction,
                "description": self.rules.scale_description,
            },
            "nace_code": base.nace_code,
            "nace_label": base.nace_label,
            "base_score_rationale": base.rationale,
            "esg_adjustments": adjustment_rationale,
            "revenue_weight_denominator": denominator_source,
        }
        return SegmentScore(
            id=self.repository._new_id("score"),
            segment_id=segment_input.row.id,
            base_score=base.score,
            adjustment_score=adjustments,
            final_score=final_score,
            weight_share=segment_input.weight_share,
            rationale=json.dumps(rationale, sort_keys=True),
        )

    def base_score_for_segment(
        self,
        nace_selection: NaceSelection | None,
        nace_candidates: tuple[NaceCandidate, ...] = (),
    ) -> BaseScoreLookup:
        nace_code = None
        nace_label = None
        if nace_selection is not None:
            nace_code = nace_selection.nace_code
            nace_label = nace_selection.nace_label
        elif nace_candidates:
            top_candidate = sorted(nace_candidates, key=lambda item: (item.rank, -item.match_score))[0]
            nace_code = top_candidate.nace_code
            nace_label = top_candidate.nace_label

        if not nace_code:
            return BaseScoreLookup(
                score=self.rules.default_base_score,
                rationale="No NACE code selected; used configured default base score.",
                nace_code=None,
                nace_label=None,
            )

        clean_code = _clean_nace_code(nace_code)
        if clean_code in self.rules.base_scores_by_code:
            return BaseScoreLookup(
                score=self.rules.base_scores_by_code[clean_code],
                rationale=f"Matched configured NACE code {clean_code}.",
                nace_code=clean_code,
                nace_label=nace_label,
            )

        division = _nace_division(clean_code)
        if division and division in self.rules.base_scores_by_division:
            return BaseScoreLookup(
                score=self.rules.base_scores_by_division[division],
                rationale=f"Matched configured NACE division {division}.",
                nace_code=clean_code,
                nace_label=nace_label,
            )

        section = self._section_for_nace_code(clean_code)
        if section and section in self.rules.base_scores_by_section:
            return BaseScoreLookup(
                score=self.rules.base_scores_by_section[section],
                rationale=f"Matched configured NACE section {section}.",
                nace_code=clean_code,
                nace_label=nace_label,
            )

        return BaseScoreLookup(
            score=self.rules.default_base_score,
            rationale=f"No scoring rule matched NACE code {clean_code}; used default base score.",
            nace_code=clean_code,
            nace_label=nace_label,
        )

    def esg_adjustment(self, factors: tuple[EsgFactor, ...]) -> tuple[float, list[dict[str, Any]]]:
        total = 0.0
        rationales: list[dict[str, Any]] = []
        for cluster_key, cluster in _cluster_esg_factors(factors).items():
            factor = _representative_factor(cluster, self.rules)
            polarity_adjustment = self.rules.polarity_adjustments.get(factor.polarity, 0.0)
            type_adjustment = self.rules.factor_type_adjustments.get(factor.factor_type, 0.0)
            raw_adjustment = polarity_adjustment + type_adjustment
            adjustment = _confidence_limited_adjustment(raw_adjustment, factor)
            total += adjustment
            rationales.append(
                {
                    "factor_id": factor.id,
                    "cluster_key": cluster_key,
                    "clustered_factor_ids": [item.id for item in cluster],
                    "factor_type": factor.factor_type,
                    "polarity": factor.polarity,
                    "esg_category": factor.esg_category or esg_category_for_factor(factor.factor_type),
                    "segment_link_type": factor.segment_link_type,
                    "score_relevant": factor.score_relevant,
                    "adjustment": adjustment,
                    "rationale": (
                        f"{factor.factor_type} ({factor.polarity}) changed score by "
                        f"{adjustment:+.2f}: {factor.description}"
                    ),
                }
            )
        capped_total = _cap_esg_adjustment(total)
        if capped_total != round(total, 6):
            rationales.append(
                {
                    "cap_applied": True,
                    "raw_adjustment": round(total, 6),
                    "capped_adjustment": capped_total,
                    "rationale": "ESG adjustment capped to avoid over-scoring repeated or outsized factors.",
                }
            )
        return capped_total, rationales

    def _revenue_denominator(
        self,
        document: Document,
        rows: list[SegmentRow],
    ) -> tuple[Decimal | None, str]:
        if document.reported_total is not None and document.reported_total > 0:
            return document.reported_total, "document_reported_total"

        reviewed_total_rows = [
            row
            for row in rows
            if row.status in REVIEWED_SCORING_STATUSES
            and is_total_row(row)
            and row.normalized_value is not None
            and row.normalized_value > 0
        ]
        if reviewed_total_rows:
            return reviewed_total_rows[0].normalized_value, "reviewed_total_row"

        reviewed_segments = [
            row
            for row in rows
            if row.status in REVIEWED_SCORING_STATUSES
            and is_scorable_business_row(row)
            and row.normalized_value is not None
            and row.normalized_value > 0
        ]
        total = sum((row.normalized_value for row in reviewed_segments), Decimal("0"))
        if total > 0:
            return total, "sum_reviewed_non_total_segments"
        return None, "missing_revenue_denominator"

    def _company_score(
        self,
        document: Document,
        scores: list[SegmentScore],
        denominator: Decimal | None,
        denominator_source: str,
    ) -> CompanyScore | None:
        weighted_scores = [
            (score.final_score, score.weight_share)
            for score in scores
            if score.weight_share is not None and score.weight_share > 0
        ]
        included_weight = sum(weight for _, weight in weighted_scores)
        if not weighted_scores or included_weight <= 0:
            return None

        weighted_average = sum(score * weight for score, weight in weighted_scores) / included_weight
        details = {
            "model_label": self.rules.model_label,
            "calculation": "sum(segment_final_score * revenue_share) / sum(included_revenue_share)",
            "denominator_source": denominator_source,
            "segment_contributions": [
                {
                    "segment_id": score.segment_id,
                    "final_score": score.final_score,
                    "weight_share": score.weight_share,
                    "weighted_contribution": (
                        None
                        if score.weight_share is None
                        else round(score.final_score * score.weight_share, 6)
                    ),
                }
                for score in scores
            ],
        }
        return CompanyScore(
            id=self.repository._new_id("company_score"),
            document_id=document.id,
            weighted_average_score=round(weighted_average, 6),
            included_weight_share=round(included_weight, 6),
            included_segment_count=len(weighted_scores),
            denominator_value=denominator,
            scale_min=self.rules.scale_min,
            scale_max=self.rules.scale_max,
            score_direction=self.rules.score_direction,
            rationale=json.dumps(details, sort_keys=True),
            created_at=self.repository._now(),
        )

    def _section_for_nace_code(self, code: str) -> str | None:
        if len(code) == 1 and code.isalpha():
            return code.upper()
        node = self._nace_by_code.get(code)
        if node is not None:
            return node.section_code
        division = _nace_division(code)
        if division is None:
            return None
        node = self._nace_by_code.get(division)
        return node.section_code if node else None


def load_scoring_rules(path: str | Path = DEFAULT_SCORING_RULES_PATH) -> ScoringRules:
    rules_path = Path(path)
    if not rules_path.exists():
        raise FileNotFoundError(f"Scoring rules file not found: {rules_path}")
    raw = json.loads(rules_path.read_text(encoding="utf-8"))
    scale = raw.get("scale", {})
    base_scores = raw.get("base_scores", {})
    adjustments = raw.get("esg_adjustments", {})
    scale_min = float(scale.get("min", 1.0))
    scale_max = float(scale.get("max", 5.0))
    if scale_min >= scale_max:
        raise ValueError("Scoring scale min must be lower than max")
    direction = str(scale.get("direction", "lower_is_better"))
    if direction not in {"lower_is_better", "higher_is_better"}:
        raise ValueError("Scoring scale direction must be lower_is_better or higher_is_better")

    return ScoringRules(
        model_label=str(raw.get("model_label", "Prototype demo score only.")),
        scale_min=scale_min,
        scale_max=scale_max,
        score_direction=direction,
        scale_description=str(scale.get("description", "")),
        default_base_score=float(raw.get("default_base_score", 3.0)),
        base_scores_by_code=_float_map(base_scores.get("codes", {})),
        base_scores_by_division=_float_map(base_scores.get("divisions", {})),
        base_scores_by_section=_float_map(base_scores.get("sections", {})),
        polarity_adjustments=_float_map(adjustments.get("polarity", {})),
        factor_type_adjustments=_float_map(adjustments.get("factor_type", {})),
        approved_esg_statuses=set(raw.get("approved_esg_statuses", ["approved", "edited"])),
    )


def is_total_row(row: SegmentRow) -> bool:
    if row.row_type == "total":
        return True
    normalized_name = re.sub(r"\s+", " ", row.segment_name.strip().lower())
    if normalized_name in TOTAL_ROW_TERMS:
        return True
    return normalized_name.startswith("total ") or normalized_name.endswith(" total")


def is_scorable_business_row(row: SegmentRow) -> bool:
    row_type = row.row_type or classify_segment_row(
        row.segment_name,
        normalized_value=row.normalized_value,
    ).row_type
    return row_type == ROW_TYPE_BUSINESS_SEGMENT and row.normalized_value is not None and row.normalized_value >= 0


def clamp_score(score: float, scale_min: float, scale_max: float) -> float:
    return round(min(max(score, scale_min), scale_max), 6)


def _weight_share(value: Decimal | None, denominator: Decimal | None) -> float | None:
    if value is None or denominator is None or denominator <= 0 or value < 0:
        return None
    return round(float(value / denominator), 6)


def _latest_esg_statuses(events: list[ReviewEvent]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for event in events:
        if not event.field_changed or not event.field_changed.startswith("esg_factor:"):
            continue
        parts = event.field_changed.split(":")
        if len(parts) == 3 and parts[2] == "status" and event.new_value:
            statuses[parts[1]] = event.new_value
    return statuses


def _clean_nace_code(code: str) -> str:
    return code.strip().upper()


def _nace_division(code: str) -> str | None:
    match = re.match(r"^(\d{2})", code)
    return match.group(1) if match else None


def _float_map(values: dict[str, Any]) -> dict[str, float]:
    return {str(key): float(value) for key, value in values.items()}


def _cluster_esg_factors(factors: tuple[EsgFactor, ...]) -> dict[str, list[EsgFactor]]:
    clusters: dict[str, list[EsgFactor]] = {}
    for factor in factors:
        if factor.score_relevant is False or factor.is_company_wide:
            continue
        key = factor.cluster_key or esg_cluster_key(
            segment_id=factor.segment_id,
            esg_category=factor.esg_category,
            factor_type=factor.factor_type,
            impact_mechanism=factor.impact_mechanism,
            page_ref=factor.evidence_source or factor.page_ref,
        )
        clusters.setdefault(key, []).append(factor)
    return clusters


def _representative_factor(
    factors: list[EsgFactor],
    rules: ScoringRules,
) -> EsgFactor:
    return sorted(
        factors,
        key=lambda factor: (
            abs(
                rules.polarity_adjustments.get(factor.polarity, 0.0)
                + rules.factor_type_adjustments.get(factor.factor_type, 0.0)
            ),
            factor.confidence or 0.0,
            factor.id,
        ),
        reverse=True,
    )[0]


def _confidence_limited_adjustment(raw_adjustment: float, factor: EsgFactor) -> float:
    confidence = factor.confidence if factor.confidence is not None else 0.5
    if confidence < 0.5:
        limit = 0.1
    elif confidence < 0.8:
        limit = 0.25
    else:
        limit = 0.5
    if (
        raw_adjustment > 0
        and factor.factor_type in SEVERE_NEGATIVE_FACTOR_TYPES
        and confidence >= 0.8
    ):
        limit = MAX_NEGATIVE_ESG_ADJUSTMENT
    return round(max(min(raw_adjustment, limit), -limit), 6)


def _cap_esg_adjustment(adjustment: float) -> float:
    return round(max(min(adjustment, MAX_NEGATIVE_ESG_ADJUSTMENT), MAX_POSITIVE_ESG_ADJUSTMENT), 6)
