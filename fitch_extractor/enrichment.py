from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal


ROW_TYPE_BUSINESS_SEGMENT = "business_segment"
ROW_TYPE_SUBTOTAL = "subtotal"
ROW_TYPE_TOTAL = "total"
ROW_TYPE_ELIMINATION = "elimination"
ROW_TYPE_OTHER_RECONCILIATION = "other_reconciliation"
ROW_TYPE_UNKNOWN = "unknown"

SEGMENT_TYPE_SINGLE_ACTIVITY = "single_activity"
SEGMENT_TYPE_MULTI_ACTIVITY = "multi_activity"
SEGMENT_TYPE_GEOGRAPHIC = "geographic_segment"
SEGMENT_TYPE_CUSTOMER = "customer_segment"
SEGMENT_TYPE_PRODUCT = "product_segment"
SEGMENT_TYPE_MIXED = "mixed_or_unclear"

SEGMENT_LINK_DIRECT = "direct_segment_name"
SEGMENT_LINK_ASSET = "asset_or_project"
SEGMENT_LINK_ACTIVITY = "activity_type"
SEGMENT_LINK_GEOGRAPHY = "geography"
SEGMENT_LINK_COMPANY_WIDE = "company_wide"
SEGMENT_LINK_UNCLEAR = "unclear"

SCORABLE_ROW_TYPES = {ROW_TYPE_BUSINESS_SEGMENT}
NON_SCORABLE_ROW_TYPES = {
    ROW_TYPE_SUBTOTAL,
    ROW_TYPE_TOTAL,
    ROW_TYPE_ELIMINATION,
    ROW_TYPE_OTHER_RECONCILIATION,
}

UNKNOWN_SEGMENT_NAMES = {
    "",
    "-",
    "n/a",
    "na",
    "unknown",
    "not disclosed",
}

TOTAL_EXACT_TERMS = {
    "total",
    "total revenue",
    "total revenues",
    "total income",
    "total net sales",
    "reported",
    "reported total",
    "consolidated",
    "consolidated total",
    "group",
    "group total",
    "grand total",
    "gesamt",
    "insgesamt",
    "summe",
    "umsatz gesamt",
    "total general",
    "chiffre d affaires total",
    "totales",
    "total ingresos",
    "totale",
    "totale ricavi",
    "totaal",
    "totale omzet",
    "samlet",
    "i alt",
    "totalt",
    "summa",
    "yhteensa",
}

TOTAL_CONTAINS_TERMS = {
    " total",
    "total ",
    "consolidated total",
    "group total",
    "total reportable",
    "reportable segments total",
}

SUBTOTAL_TERMS = {
    "subtotal",
    "sub total",
    "sub-total",
    "reportable segments",
    "operating segments",
    "segment total",
    "total segments",
    "sum reportable",
    "zwischensumme",
    "teilsumme",
    "sous total",
    "sous-total",
    "subtotal segmentos",
    "subtotale",
    "subtotaal",
    "delsumma",
}

ELIMINATION_TERMS = {
    "elimination",
    "eliminations",
    "intercompany",
    "inter company",
    "inter-company",
    "intragroup",
    "intra group",
    "intra-group",
    "intersegment",
    "inter segment",
    "intra-segment",
    "eliminierung",
    "eliminierungen",
    "konzernintern",
    "interne umsatze",
    "eliminacion",
    "eliminaciones",
    "eliminacao",
    "eliminacoes",
    "eliminazioni",
    "eliminaties",
    "eliminering",
    "elimineringar",
    "eliminoinnit",
}

RECONCILIATION_TERMS = {
    "reconciliation",
    "reconciling",
    "adjustment",
    "adjustments",
    "business interruption proceeds",
    "insurance proceeds",
    "property insurance proceeds",
    "government grant income",
    "grant income",
    "unallocated",
    "not allocated",
    "corporate",
    "head office",
    "central",
    "other items",
    "reclassification",
    "reclassifications",
    "hedging",
    "fair value",
    "discontinued",
    "incidental",
    "other reconciling",
    "uberleitung",
    "abstimmung",
    "anpassung",
    "nicht zugeordnet",
    "rapprochement",
    "ajustement",
    "non alloue",
    "conciliacion",
    "reconciliacion",
    "ajuste",
    "no asignado",
    "riconciliazione",
    "rettifica",
    "non allocato",
    "reconciliatie",
    "aanpassing",
    "niet toegewezen",
    "avstemming",
    "justering",
    "ikke allokeret",
}

GEOGRAPHIC_TERMS = {
    "north america",
    "south america",
    "latin america",
    "europe",
    "emea",
    "apac",
    "asia pacific",
    "asia",
    "africa",
    "middle east",
    "oceania",
    "international",
    "domestic",
    "united states",
    "usa",
    "canada",
    "china",
    "japan",
    "india",
    "germany",
    "france",
    "spain",
    "italy",
    "uk",
    "united kingdom",
    "denmark",
    "norway",
    "sweden",
    "finland",
    "netherlands",
    "benelux",
}

CUSTOMER_TERMS = {
    "consumer",
    "consumers",
    "commercial",
    "enterprise",
    "institutional",
    "wholesale",
    "retail customers",
    "small business",
    "sme",
    "public sector",
    "government",
    "private clients",
}

PRODUCT_TERMS = {
    "products",
    "product",
    "services",
    "solutions",
    "software",
    "hardware",
    "platform",
    "devices",
    "subscriptions",
    "equipment",
}

ACTIVITY_TERMS = {
    "insurance",
    "banking",
    "lending",
    "manufacturing",
    "production",
    "generation",
    "distribution",
    "transmission",
    "construction",
    "mining",
    "retail",
    "transport",
    "shipping",
    "logistics",
    "software",
    "hosting",
    "telecom",
    "pharmaceutical",
    "healthcare",
    "steel",
    "cement",
    "chemicals",
    "power",
    "wind",
    "solar",
    "oil",
    "gas",
}

MIXED_MARKERS = {
    "&",
    "/",
    ",",
    " and ",
    " plus ",
    " other",
    " others",
    " miscellaneous",
    " diversified",
    "various",
    "multiple",
}


@dataclass(frozen=True)
class SegmentClassification:
    row_type: str
    segment_type: str
    segment_name_original: str
    segment_name_normalized: str
    language: str | None
    needs_review: bool
    rationale: str


@dataclass(frozen=True)
class EvidenceBundle:
    evidence_original: str
    evidence_translation: str | None
    language: str | None
    page_numbers: tuple[int, ...]
    classification_text: str
    reasoning: str


def classify_segment_row(
    segment_name: str,
    *,
    evidence_text: str = "",
    language: str | None = None,
    normalized_value: Decimal | None = None,
) -> SegmentClassification:
    name_original = segment_name.strip()
    name_normalized = normalize_english_meaning(name_original)
    combined_text = normalize_english_meaning(" ".join((name_original, evidence_text)))
    row_type = classify_row_type(name_original, normalized_value=normalized_value)
    segment_type = (
        classify_business_segment_type(name_original, evidence_text=evidence_text)
        if row_type == ROW_TYPE_BUSINESS_SEGMENT
        else SEGMENT_TYPE_MIXED
    )
    inferred_language = language or detect_language_hint(" ".join((name_original, evidence_text)))
    needs_review = _needs_review(row_type, segment_type, name_normalized)
    rationale = _classification_rationale(row_type, segment_type, combined_text, needs_review)
    return SegmentClassification(
        row_type=row_type,
        segment_type=segment_type,
        segment_name_original=name_original,
        segment_name_normalized=name_normalized,
        language=inferred_language,
        needs_review=needs_review,
        rationale=rationale,
    )


def classify_row_type(
    segment_name: str,
    *,
    normalized_value: Decimal | None = None,
) -> str:
    normalized = normalize_english_meaning(segment_name)
    if normalized in UNKNOWN_SEGMENT_NAMES:
        return ROW_TYPE_UNKNOWN
    if normalized in TOTAL_EXACT_TERMS or any(
        normalized.startswith(term.strip()) or normalized.endswith(term.strip())
        for term in TOTAL_CONTAINS_TERMS
    ):
        return ROW_TYPE_TOTAL
    if any(term in normalized for term in ELIMINATION_TERMS):
        return ROW_TYPE_ELIMINATION
    if any(term in normalized for term in SUBTOTAL_TERMS):
        return ROW_TYPE_SUBTOTAL
    if any(term in normalized for term in RECONCILIATION_TERMS):
        return ROW_TYPE_OTHER_RECONCILIATION
    if normalized_value is not None and normalized_value < 0:
        return ROW_TYPE_OTHER_RECONCILIATION
    return ROW_TYPE_BUSINESS_SEGMENT


def classify_business_segment_type(segment_name: str, *, evidence_text: str = "") -> str:
    normalized_name = normalize_english_meaning(segment_name)
    normalized_evidence = normalize_english_meaning(evidence_text)
    combined = " ".join((normalized_name, normalized_evidence)).strip()
    if not normalized_name or normalized_name in {"other", "others", "miscellaneous"}:
        return SEGMENT_TYPE_MIXED
    if any(term == normalized_name or term in normalized_name for term in GEOGRAPHIC_TERMS):
        return SEGMENT_TYPE_GEOGRAPHIC
    if any(term in normalized_name for term in CUSTOMER_TERMS):
        return SEGMENT_TYPE_CUSTOMER
    if any(marker in f" {normalized_name} " for marker in MIXED_MARKERS):
        return SEGMENT_TYPE_MULTI_ACTIVITY
    if any(term in normalized_name for term in PRODUCT_TERMS) and not any(
        term in normalized_name for term in ACTIVITY_TERMS
    ):
        return SEGMENT_TYPE_PRODUCT
    if any(term in combined for term in ACTIVITY_TERMS):
        return SEGMENT_TYPE_SINGLE_ACTIVITY
    return SEGMENT_TYPE_MIXED


def build_evidence_bundle(
    *,
    evidence_items: list[tuple[int, str, str | None]],
    context: str = "",
) -> EvidenceBundle:
    parts: list[str] = []
    page_numbers: list[int] = []
    languages: list[str] = []
    for page_number, text, language in evidence_items:
        stripped = text.strip()
        if not stripped:
            continue
        page_numbers.append(page_number)
        if language:
            languages.append(language)
        parts.append(f"Page {page_number}: {stripped}")
    if context.strip():
        parts.append(context.strip())

    evidence_original = "\n".join(parts)
    language = _most_common(languages) or detect_language_hint(evidence_original)
    evidence_translation = None
    classification_text = evidence_original
    if language and language != "en":
        evidence_translation = normalize_english_meaning(evidence_original)
        classification_text = evidence_translation

    return EvidenceBundle(
        evidence_original=evidence_original,
        evidence_translation=evidence_translation,
        language=language,
        page_numbers=tuple(sorted(set(page_numbers))),
        classification_text=classification_text,
        reasoning="Evidence bundle combines the revenue row, nearby pages, and parsed page language hints.",
    )


def normalize_english_meaning(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    ascii_text = ascii_text.casefold().replace("—", " ").replace("–", " ")
    ascii_text = re.sub(r"[^a-z0-9&/,+.\-\s]", " ", ascii_text)
    ascii_text = re.sub(r"\s+", " ", ascii_text).strip()
    replacements = {
        "i alt": "total",
        "yhteensa": "total",
        "gesamt": "total",
        "insgesamt": "total",
        "summe": "total",
        "totale": "total",
        "totaal": "total",
        "totalt": "total",
        "summa": "total",
        "eliminierungen": "eliminations",
        "eliminering": "eliminations",
        "elimineringar": "eliminations",
        "eliminaciones": "eliminations",
        "eliminacoes": "eliminations",
        "eliminazioni": "eliminations",
        "eliminaties": "eliminations",
        "reconciliacion": "reconciliation",
        "conciliacion": "reconciliation",
        "uberleitung": "reconciliation",
        "abstimmung": "reconciliation",
        "rapprochement": "reconciliation",
        "riconciliazione": "reconciliation",
        "reconciliatie": "reconciliation",
        "niet toegewezen": "unallocated",
        "nicht zugeordnet": "unallocated",
        "non alloue": "unallocated",
        "no asignado": "unallocated",
        "ikke allokeret": "unallocated",
    }
    for source, target in replacements.items():
        ascii_text = re.sub(rf"\b{re.escape(source)}\b", target, ascii_text)
    return re.sub(r"\s+", " ", ascii_text).strip()


def detect_language_hint(text: str) -> str | None:
    raw_normalized = unicodedata.normalize("NFKD", text)
    raw_ascii = "".join(ch for ch in raw_normalized if not unicodedata.combining(ch))
    raw_ascii = re.sub(r"\s+", " ", raw_ascii.casefold()).strip()
    normalized = normalize_english_meaning(text)
    if not normalized:
        return None
    if any(term in raw_ascii for term in ("nicht ", "gesamt", "umsatz", "uberleitung")):
        return "de"
    if any(term in raw_ascii for term in ("chiffre d affaires", "non alloue", "sous total")):
        return "fr"
    if any(term in raw_ascii for term in ("ingresos", "no asignado", "reconciliacion")):
        return "es"
    if any(term in raw_ascii for term in ("ricavi", "non allocato", "riconciliazione")):
        return "it"
    if any(term in raw_ascii for term in ("omzet", "niet toegewezen", "reconciliatie")):
        return "nl"
    if any(term in raw_ascii for term in ("i alt", "ikke allokeret")):
        return "da"
    return "en"


def is_business_segment(row_type: str | None) -> bool:
    return row_type == ROW_TYPE_BUSINESS_SEGMENT or row_type is None


def score_relevant_esg_link(link_type: str | None, is_company_wide: bool) -> bool:
    if is_company_wide:
        return False
    return link_type in {
        SEGMENT_LINK_DIRECT,
        SEGMENT_LINK_ASSET,
        SEGMENT_LINK_ACTIVITY,
        SEGMENT_LINK_GEOGRAPHY,
    }


def esg_category_for_factor(factor_type: str) -> str:
    environmental = {
        "emissions_target",
        "decarbonization_plan",
        "renewable_investment",
        "coal_phaseout",
        "fossil_fuel_exposure",
        "circular_economy",
        "biodiversity_impact",
        "water_risk",
    }
    social = {"social_program", "labor_issue", "safety_incident"}
    governance = {"governance_policy", "company_wide_policy", "controversy", "regulatory_violation"}
    if factor_type in environmental:
        return "E"
    if factor_type in social:
        return "S"
    if factor_type in governance:
        return "G"
    return "unknown"


def default_esg_link_type(
    *,
    is_company_wide: bool,
    segment_name: str | None,
    linked_business_activity: str | None,
) -> str:
    if is_company_wide:
        return SEGMENT_LINK_COMPANY_WIDE
    if segment_name:
        return SEGMENT_LINK_DIRECT
    if linked_business_activity:
        return SEGMENT_LINK_ACTIVITY
    return SEGMENT_LINK_UNCLEAR


def esg_cluster_key(
    *,
    segment_id: str | None,
    esg_category: str | None,
    factor_type: str,
    impact_mechanism: str | None,
    page_ref: str | None,
) -> str:
    mechanism = normalize_english_meaning(impact_mechanism or factor_type)
    source = normalize_english_meaning(page_ref or "unknown_source")
    category = esg_category or esg_category_for_factor(factor_type)
    return "|".join((segment_id or "company", category, factor_type, mechanism, source))


def _needs_review(row_type: str, segment_type: str, normalized_name: str) -> bool:
    if row_type in {ROW_TYPE_UNKNOWN, ROW_TYPE_OTHER_RECONCILIATION}:
        return True
    if row_type in NON_SCORABLE_ROW_TYPES:
        return False
    if segment_type in {
        SEGMENT_TYPE_MULTI_ACTIVITY,
        SEGMENT_TYPE_GEOGRAPHIC,
        SEGMENT_TYPE_CUSTOMER,
        SEGMENT_TYPE_PRODUCT,
        SEGMENT_TYPE_MIXED,
    }:
        return True
    return normalized_name in {"other", "others"}


def _classification_rationale(
    row_type: str,
    segment_type: str,
    combined_text: str,
    needs_review: bool,
) -> str:
    detail = f"row_type={row_type}; segment_type={segment_type}"
    if needs_review:
        detail += "; review required before relying on enrichment"
    if len(combined_text) > 0:
        detail += "; based on segment label and available evidence text"
    return detail


def _most_common(values: list[str]) -> str | None:
    if not values:
        return None
    counts = {value: values.count(value) for value in set(values)}
    return sorted(counts, key=lambda value: (-counts[value], value))[0]
