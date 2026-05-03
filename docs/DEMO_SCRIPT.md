# Demo Script

1. Start the app:

```bash
.venv/bin/streamlit run streamlit_app.py
```

2. Upload PDF:

Open the sidebar, upload an annual report or 10-K PDF, enter company name, fiscal period, currency, and scale.

3. Run analysis:

Choose `anthropic - real LLM extraction` for the real pipeline or `fake - deterministic local smoke test` for a no-API demo. Click `Start analysis`.

4. Inspect evidence:

Open `Segment Review`, select a row, and expand `Evidence`. Point out page number, parser source, snippet, and validation flags.

5. Fix and review rows:

Edit any incorrect values in the table, save edits, approve valid rows, and reject non-segment or duplicate rows.

6. Approve:

Resolve or acknowledge validation issues. Click `Approve document` once blockers are cleared.

7. Export:

Click `Create export files` or run:

```bash
.venv/bin/python scripts/export_document.py doc_...
```

Show `exports/<document_id>/revenue_segments.csv`, `revenue_segments.xlsx`, and `audit_export.json`.

8. Show evaluation:

```bash
.venv/bin/python -m fitch_extractor.evaluate --gold 'data/gold/*.csv' --pred exports
```

Open `reports/evaluation_summary.md` and `reports/failure_analysis.md`.
