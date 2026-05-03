# Streamlit Community Cloud Setup

Streamlit Community Cloud can run the interactive `streamlit_app.py` workbench. Configure it from
the GitHub repository and keep secrets in Streamlit, not in committed files.

## App Settings

- Repository: this project repository.
- Branch: `main` or the branch you deploy from.
- Main file path: `streamlit_app.py`.
- Python dependencies: `requirements.txt`.

No extra packages file is required for the current dependency set.

## Secrets

Set secrets through Streamlit Community Cloud, not in the repository.

Minimum real-extraction configuration:

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
FITCH_EXTRACTION_PROVIDER = "anthropic"
FITCH_EXTRACTION_MODEL = "claude-sonnet-4-6"
FITCH_ENABLE_SECOND_PASS_VERIFICATION = "true"
FITCH_ENABLE_ARBITRATION = "false"
```

For a no-API smoke deployment, use:

```toml
FITCH_EXTRACTION_PROVIDER = "fake"
FITCH_ENABLE_ARBITRATION = "false"
```

Only set `FITCH_ARBITRATION_MODEL` when arbitration is intentionally enabled and the selected
model is available to the account.

## Runtime Storage

The prototype stores its SQLite database, uploaded PDFs, generated evidence previews, and exports
on the app filesystem. On hosted Streamlit, that filesystem should be treated as ephemeral. For a
production deployment, move those artifacts to managed storage and keep the existing review/export
contracts.

## Verification

After deployment:

1. Open the app.
2. Confirm the sidebar loads without a provider error.
3. For fake mode, upload a small PDF and click `Queue extraction`.
4. Click `Process next queued document`.
5. Review the segment rows, approve or reject all rows, approve the document, and create exports.
6. For Anthropic mode, repeat the same flow with a real annual report after confirming the key is
   configured in Streamlit secrets.

Export remains blocked until the document approval gate is satisfied.
