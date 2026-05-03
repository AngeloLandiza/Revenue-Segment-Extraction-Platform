# Prototype Scoring Model

This scoring layer is for local class/project demonstration only. It is not an official Fitch Ratings score, Sustainable Fitch score, ESG rating, credit rating, or investment recommendation.

## Configuration

Rules live in `config/scoring_rules.yaml`. The file is JSON-compatible YAML so the prototype can load it with the Python standard library and avoid adding a parser dependency.

Default scale:

- `1` is best / lower impact.
- `5` is worst / higher impact.
- Positive ESG factors improve the score by lowering it.
- Negative ESG factors worsen the score by raising it.
- Segment final scores are capped to the configured minimum and maximum.

Configurable fields:

- `scale.min`
- `scale.max`
- `scale.direction`
- `default_base_score`
- `base_scores.sections`
- `base_scores.divisions`
- `base_scores.codes`
- `esg_adjustments.polarity`
- `esg_adjustments.factor_type`
- `approved_esg_statuses`

## Segment Score

For each approved or edited non-total segment row:

```text
base score = configured NACE code/division/section score, or default fallback
ESG adjustment = sum(reviewed segment-linked ESG polarity and factor-type adjustments)
final score = capped(base score + ESG adjustment)
```

NACE lookup order:

1. Reviewer-selected NACE code.
2. Top ranked NACE candidate if no reviewer selection exists.
3. Exact code rule.
4. Division rule.
5. Section rule from the NACE reference hierarchy.
6. Configured default base score.

Only ESG factors with review status `approved` or `edited` are included. Rejected, pending, company-wide, and unlinked ESG factors are excluded from segment score adjustments.

Each `SegmentScore.rationale` stores JSON with the model label, scale description, NACE rationale, ESG adjustment details, and revenue denominator source.

## Revenue Weights

Rejected rows are always excluded. Unreviewed rows are excluded from persisted scoring unless a future workflow explicitly labels draft scoring.

Total rows are excluded from segment scoring to avoid double-counting. Denominator selection is:

1. `documents.reported_total`, when available and positive.
2. A reviewed total row, when available and positive.
3. Sum of reviewed, non-total segment rows.

Each segment weight is:

```text
segment normalized revenue / selected denominator
```

Rows without positive normalized revenue can receive a segment score but do not contribute to the company weighted average.

## Company Score

The company score is:

```text
sum(segment final score * segment revenue share) / sum(included segment revenue shares)
```

The summary stores:

- weighted average score
- included revenue share
- included segment count
- denominator value
- scale bounds and direction
- calculation rationale JSON

## Verification

Run:

```bash
.venv/bin/python -m unittest tests.test_scoring_service
.venv/bin/python -m unittest discover -s tests
```

Manual check:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Open the `Scoring` tab, click `Compute prototype scores`, confirm the warning label appears, review the score table, then create export files after document approval.
