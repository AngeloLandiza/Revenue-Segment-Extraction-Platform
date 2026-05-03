# ESG Extraction

## Purpose

The ESG extension extracts evidence-backed ESG factors from annual reports and attaches them to revenue segments only when the evidence explicitly supports that link. ESG factors are review candidates, not ground truth, and they do not override the core revenue extraction workflow.

## Pipeline

1. Deterministic retrieval scans parsed pages for ESG terms such as emissions targets, decarbonization, renewable investment, fossil-fuel exposure, safety incidents, labor issues, biodiversity, water risk, and regulatory violations.
2. Retrieval boosts ESG pages near known segment pages and pages in MD&A, business review, or risk sections.
3. Generic ESG indexes, contents pages, cross-reference pages, and boilerplate governance text are filtered before LLM calls when possible.
4. The LLM receives only selected candidate pages and known segment names.
5. The LLM must return strict `EsgExtractionOutput` JSON with evidence, page reference, factor type, polarity, confidence, company-wide classification, and linkage rationale.
6. Python post-processing enforces segment-link rules before persistence.
7. Reviewers edit, unlink, relink, approve, or reject factors in the Streamlit ESG tab.
8. Exports include approved/edited segment-linked summaries in the main CSV/XLSX and all ESG records in JSON audit export.

## Controlled Vocabulary

Supported `factor_type` values:

- `emissions_target`
- `decarbonization_plan`
- `renewable_investment`
- `fossil_fuel_exposure`
- `coal_phaseout`
- `controversy`
- `regulatory_violation`
- `safety_incident`
- `social_program`
- `labor_issue`
- `circular_economy`
- `biodiversity_impact`
- `water_risk`
- `governance_policy`
- `company_wide_policy`
- `other`

Supported `polarity` values:

- `positive`
- `negative`
- `neutral`
- `mixed`
- `unknown`

## Segment Linking Rule

An ESG factor is linked to a segment only when evidence explicitly names the segment or clearly ties the ESG action, risk, target, controversy, or impact to that segment's business activity, asset type, product line, geography, or named project.

If linkage is uncertain, the factor is stored as company-wide with `segment_id = null` and `is_company_wide = true`. Company-wide ESG is never copied to all segments automatically.

Generic ESG indexes, generic governance statements, and cross-reference text are discarded unless the evidence is material and linked.

## Stored Fields

`EsgFactor` records store:

- `document_id`
- `segment_id` nullable
- `factor_type`
- `polarity`
- `description`
- `page_ref`
- `evidence_text`
- `confidence`
- `is_company_wide`

Review status is derived from review events:

- `pending`
- `approved`
- `edited`
- `rejected`

## Review Workflow

In Streamlit:

1. Open the `ESG Factors` tab.
2. Review `Segment-linked ESG` separately from `Company-wide ESG`.
3. Edit factor type, polarity, description, page reference, evidence, or confidence if needed.
4. Use `Unlink` when a segment link is unsupported.
5. Use `Relink` only when the evidence explicitly supports the selected segment.
6. Approve factors that are evidence-backed and correctly linked.
7. Reject factors that are unsupported, boilerplate, duplicated, or not useful for scoring.

All actions write review events with reviewer, action, old value, new value, note, and timestamp.

## Verification

Run:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall revenue_segment_extractor tests streamlit_app.py
```

Relevant tests are in `tests/test_esg_extraction.py`.
