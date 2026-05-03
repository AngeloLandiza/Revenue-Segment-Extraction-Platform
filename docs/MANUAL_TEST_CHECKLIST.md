# Manual Test Checklist

## Setup

- [ ] Create and activate a virtual environment.
- [ ] Install `requirements.txt`.
- [ ] Run `scripts/manage_db.py`.
- [ ] Set `ANTHROPIC_API_KEY` for real extraction or `FITCH_EXTRACTION_PROVIDER=fake` for smoke testing.

## Upload

- [ ] Start Streamlit.
- [ ] Upload an annual report or 10-K PDF.
- [ ] Enter company name, fiscal period, currency, and scale.

## Analyze

- [ ] Click `Start analysis`.
- [ ] Confirm parsing, extraction, NACE, ESG, validation, and review stages complete or show actionable warnings.

## Review

- [ ] Inspect each segment row.
- [ ] Open evidence snippets and confirm page references.
- [ ] Edit incorrect values.
- [ ] Reject non-segment rows.
- [ ] Add a missing manual row if needed.

## Validation

- [ ] Confirm validation failures are visible.
- [ ] Resolve blocking errors after fixing evidence/data.
- [ ] Acknowledge reconciliation mismatches only with a reviewer note.

## Approve

- [ ] Approve or edit every valid row.
- [ ] Reject invalid rows.
- [ ] Review NACE candidates or overrides.
- [ ] Review ESG factors.
- [ ] Click `Approve document`.

## Export

- [ ] Confirm export is disabled before document approval.
- [ ] Create export files after approval.
- [ ] Confirm rejected rows are absent from final CSV/XLSX.
- [ ] Confirm audit JSON includes evidence, validation issues, review events, and rejected rows.

## Evaluation

- [ ] Run `python -m fitch_extractor.evaluate --gold ... --pred exports`.
- [ ] Open `reports/evaluation_summary.md`.
- [ ] Open `reports/failure_analysis.md`.
