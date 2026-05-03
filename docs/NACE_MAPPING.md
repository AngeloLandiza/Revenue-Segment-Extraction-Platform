# NACE Rev.2 Mapping

## Reference CSV Path

The prompt placeholder `{{NACE_REV2_OUTLINE_CSV_PATH}}` has been replaced with the repo-local default path:

```text
reference/NACE_Rev2_Outline.csv
```

The CSV was copied from the local source file:

```text
/Users/angelolandiza/Documents/CS 294 Project Revamp/data/NACE_Rev2 - Outline.csv
```

Application code does not depend on that absolute source path. Runtime resolution uses:

1. An explicit path passed to `load_nace_nodes(...)`.
2. The `FITCH_NACE_REV2_CSV_PATH` environment variable.
3. The repo default `reference/NACE_Rev2_Outline.csv`.

## Detected CSV Schema

The loader scans past title, instruction, and blank rows until it finds a header row containing:

- `level_depth`
- `level`
- `node_code`
- `node_name`

The detected full schema is:

- `level_depth`
- `level`
- `node_code`
- `node_name`
- `node_key`
- `parent_key`
- `section_code`
- `section_name`
- `division_code`
- `division_name`
- `group_code`
- `group_name`
- `class_code`
- `class_name`
- `hierarchy_path_codes`
- `hierarchy_path_names`
- `source_row_number`

Supported hierarchy levels are `section`, `division`, `group`, and `class`.

## Mapping Flow

`NaceMappingService` loads the reference into internal `NaceNode` records. Each node preserves the CSV hierarchy fields and exposes normalized code, label, level, parent, hierarchy path, and source row metadata.

For each reviewable segment row, the service combines:

- company name
- document name
- segment name
- persisted evidence snippets
- nearby parsed report pages around the segment evidence page
- optional caller-supplied context

Candidate generation is deterministic but broad:

- normalized token overlap against labels and hierarchy paths
- fuzzy label similarity with Python standard library matching
- keyword-overlap rationale
- a small specificity bonus for more granular levels
- domain hints that add obvious utility, power-generation, network-operation, hosting/cloud, and energy-distribution candidates when supported by segment/report context

The top broad candidates are then sent to the LLM classifier through the existing provider interface using prompt version `nace_mapping_v2`. The LLM must return strict JSON with:

- `decision`: `mapped`, `not_applicable`, or `needs_review`
- `selected_code`: a code from the supplied candidate list, or `null`
- `confidence`
- `rationale`
- ranked candidate rationales

The LLM may not invent NACE codes. Any returned code outside the supplied candidate list raises `ValueError`.

Rows such as totals, subtotals, eliminations, reportable-segment roll-ups, reported-only rows, reclassifications, reconciling items, and hedging rows are treated as `not_applicable` and do not receive a default NACE selection.

The service stores up to five ranked candidates in `nace_candidates`. It creates or replaces an automatic `segment_nace_selections` row only when:

- the LLM decision is `mapped`
- the selected code is in the stored candidate set
- confidence is at least `0.65`
- the current selection is absent or was an earlier automatic selection

Human reviewer selections and overrides are preserved on rerun.

If no provider is configured, the service falls back to deterministic candidates but only auto-selects when the top deterministic score is strong enough. Streamlit now passes the selected provider to NACE mapping, including fake mode for local tests and Anthropic mode for real LLM classification.

## Review Override

Reviewers can accept a stored candidate through `ReviewService.accept_nace_candidate(...)` or manually override the selected mapping through `ReviewService.override_segment_nace(...)`.

Override writes:

- selected code, label, level, match score, and rationale in `segment_nace_selections`
- `source = reviewer_override`
- reviewer identity and timestamp
- a `ReviewEvent` with `action = override_nace_code`

NACE mapping is optional by default. It does not block revenue extraction, segment approval, document approval, or final export unless a future configuration explicitly adds that gate.

## Known Mapping Corrections

The previous deterministic-only default produced weak mappings such as:

- Ørsted `Offshore` -> `65.12 Non-life insurance`
- Ørsted `Onshore` -> `65.20 Reinsurance`
- Alliander `Network operator Liander` -> `79.12 Tour operator activities`
- total/elimination rows mapped to operating NACE codes

The new workflow uses nearby report context and not-applicable decisions to avoid those automatic selections. For example, Ørsted offshore/onshore power-generation context can support `35.11 Production of electricity`, while Alliander network-operator context can support `35.13 Distribution of electricity` when the evidence mentions energy transport, connection, and metering services.

## Verification

Run:

```bash
.venv/bin/python -m unittest tests.test_nace_mapping
.venv/bin/python -m unittest discover -s tests
```

The focused NACE tests cover reference loading, candidate generation, invalid invented-code rejection, strict LLM mapping, context-assisted power-generation mapping, not-applicable row handling, preservation of reviewer overrides, candidate storage, reviewer override, and export fields.
