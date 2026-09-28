# Personal Google Sheets migration

This fork uses Aurum/PostgreSQL as the source of truth. Google Sheets is read
only by the one-time importer; it is not part of the production write path.

## Run locally

1. Install Docker Desktop (it supplies `docker compose`), then copy the local
   configuration:

   ```bash
   cp .env.example .env
   ```

2. Before the first start, replace `AURUM_POSTGRES_PASSWORD=change-me` in
   `.env` with a password. Keep `AURUM_BIND_ADDRESS=127.0.0.1` for a local-only
   instance.

3. Start and verify:

   ```bash
   docker compose up -d --build
   docker compose ps
   curl --fail http://localhost:3000/api/health
   ```

   The health response is `{"status":"ok"}`. Open http://localhost:3000.

## Import Personal Expenses

The importer downloads the `Transactions` and `Lists` tabs from the supplied
Google Sheet. The sheet must be readable by the machine performing the import.
For a private Sheet, obtain a short-lived OAuth token with the Google Sheets
read-only scope and pass it via the environment; the importer calls the Google
Sheets API directly. A public sheet needs no token and uses CSV export.

```bash
export GOOGLE_SHEETS_ACCESS_TOKEN='short-lived-oauth-token'
python3 backend/scripts/import_google_sheet.py \
  --sheet-url 'https://docs.google.com/spreadsheets/d/1U5Rn7FgpGywAHBytJop52RLic7AVlNQzietsgI9xwF8/edit?usp=sharing' \
  --api-url http://localhost:3000/api
```

Run it first with `--dry-run` if you want to validate connection and mapping
without writes. When HTTP Basic Auth is enabled, append `--username` and
`--password`; do not put the password into a shell history on a shared machine.

Without currency conversion, the importer creates the requested initial
accounts (Cash GEL, Georgian Card GEL, Mono UAH, Wise USD) and all expense
categories in `Lists`. A transaction with an empty payment method goes to
`Unspecified <currency>`; `Другое` goes to `Other <currency>`. This makes the
uncertainty visible and avoids guessing.

Each Google `Transaction ID` is written to `transactions.external_id`, a unique
nullable column. Re-running the command searches that ID first and reports it
as already present, so it does not duplicate expenses. Long receipt OCR is kept
in `notes`; short human-readable text/merchant becomes `description`; the
source becomes a `source:<value>` tag. A receipt URL, when present, is appended
to notes.

### Fixed GEL → USD conversion

To keep this personal instance entirely in USD at a fixed rate, use
`--gel-to-usd`. Existing imported rows can be rewritten safely with
`--update-existing`, still matched only by their Google `Transaction ID`:

For this instance, the agreed rate is `0.3846153846` USD per GEL. It is derived
from the two original 780 GEL apartment rows in `Transactions`, each of which
is confirmed as $300.00: `300 / 780 = 0.3846153846`.

```bash
python3 backend/scripts/import_google_sheet.py \
  --sheet-url 'https://docs.google.com/spreadsheets/d/1U5Rn7FgpGywAHBytJop52RLic7AVlNQzietsgI9xwF8/edit?usp=sharing' \
  --api-url http://localhost:3001/api \
  --gel-to-usd 0.3846153846 \
  --update-existing
```

This conversion applies only to GEL and leaves USD unchanged. It rejects EUR
or UAH rather than fabricating an exchange rate for them.

## Upstream and backups

This checkout has `origin` pointing to the personal fork and `upstream` to
`Zproger/Aurum`. Before importing a new upstream release, export a JSON backup
from **Settings → Backup & Restore**.

```bash
git fetch upstream
git switch main
git merge upstream/main
git push origin main
docker compose up -d --build
```

For a running local instance, `docker compose down` stops it but preserves the
database volume. Do not use `docker compose down -v` unless you deliberately
want to erase that database; use the in-app JSON backup first.
