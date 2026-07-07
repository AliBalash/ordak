# Troubleshooting

## Chrome session is not detected

- Make sure regular `Google Chrome` is already running.
- Confirm the account you want to use is already signed in inside that same Chrome session.
- Check `BROWSER_USER_DATA_DIR` and `BROWSER_PROFILE_NAME` in `.env`.

## Apple Events access fails on macOS

- In Chrome, enable `View > Developer > Allow JavaScript from Apple Events`.
- If needed, close and reopen Chrome after enabling it.

## Provider tab opens but no answer is extracted

- Provider DOMs can change and selectors may need updates.
- Check `/diagnostics` for recent failures.
- Review artifacts under `app/storage/screenshots/`, `app/storage/logs/`, and `app/storage/traces/`.

## Image generation completed but no output image is returned

- `ordak` intentionally prefers false negatives over returning the wrong asset.
- If extraction confidence is low, the job can finish without `output_images`.
- Inspect stored screenshots and logs to see what the provider actually rendered.

## Local API works but Docker workflow does not

- The main workflow depends on your local signed-in desktop Chrome session.
- Containerized runs are not the primary target for this project.
- Prefer the native local setup on macOS for reliable provider interaction.

## Tests do not run

- Create the venv first: `python3 -m venv .venv`
- Install dependencies: `.venv/bin/pip install -r requirements.txt`
- Run tests with: `.venv/bin/python -m pytest -q`

## Storage grows too large

- Run `python scripts/cleanup_storage.py`
- Review retention-related values in `.env`:
  - `STORAGE_RETENTION_DAYS`
  - `MAX_TRACES`
  - `MAX_FAILURE_HTML_DUMPS`
