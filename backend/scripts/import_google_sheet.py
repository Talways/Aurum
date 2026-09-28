"""One-time, idempotent Google Sheets -> Aurum importer.

Run from the host after Aurum is up::

    python backend/scripts/import_google_sheet.py \
      --sheet-url 'https://docs.google.com/spreadsheets/d/.../edit' \
      --api-url http://localhost:3000/api

The sheet must be readable by the machine running this command. Each source
``Transaction ID`` becomes ``transactions.external_id``; a repeated run skips
that ID instead of creating a duplicate.
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


SHEETS_EPOCH = date(1899, 12, 30)  # Google Sheets / Excel serial-date epoch.
DEFAULT_TRANSACTIONS_GID = "918403041"
DEFAULT_LISTS_GID = "1000002"
DEFAULT_ACCOUNTS = (
    ("Cash GEL", "cash", "GEL"),
    ("Georgian Card GEL", "debit_card", "GEL"),
    ("Mono UAH", "debit_card", "UAH"),
    ("Wise USD", "checking", "USD"),
)
SOURCE_PREFIX = "source:"
IMPORT_TAG = "import:google-sheets"
CATEGORY_COLORS = ["#2A78D6", "#22A06B", "#D97706", "#C2410C", "#7C3AED", "#DB2777"]


class ImportErrorWithContext(RuntimeError):
    pass


def clean(value: str | None) -> str:
    return (value or "").strip()


def parse_sheet_date(value: str) -> str:
    """Return ISO calendar date for an ISO/text date or Sheets serial number."""
    value = clean(value)
    if not value:
        raise ValueError("date is empty")
    try:
        serial = Decimal(value)
    except InvalidOperation:
        serial = None
    if serial is not None:
        return (SHEETS_EPOCH + timedelta(days=int(serial))).isoformat()
    normalized = value.replace("Z", "+00:00")
    for parser in (date.fromisoformat, lambda v: datetime.fromisoformat(v).date()):
        try:
            return parser(normalized).isoformat()
        except ValueError:
            pass
    for format_ in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, format_).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"unsupported date {value!r}")


def short_description(row: dict[str, str]) -> str:
    description = clean(row.get("Description"))
    merchant = clean(row.get("Merchant"))
    category = clean(row.get("Category"))
    if description and len(description) <= 255:
        return description
    if merchant and merchant.casefold() not in {"хз", "unknown", "n/a"}:
        return merchant[:255]
    suffix = "receipt" if clean(row.get("Source")).casefold() == "receipt" else "expense"
    return f"{category or 'Uncategorized'} — {suffix}"[:255]


def notes(row: dict[str, str]) -> str | None:
    parts: list[str] = []
    description = clean(row.get("Description"))
    # A short description has already been retained in description; long OCR
    # text is stored only here, where it remains searchable and untruncated.
    if len(description) > 255:
        parts.append(description)
    receipt_url = clean(row.get("Receipt URL"))
    if receipt_url:
        parts.append(f"Receipt URL: {receipt_url}")
    result = "\n\n".join(parts)
    if len(result) > 60000:
        raise ValueError("notes exceed Aurum's 60,000-character receipt limit")
    return result or None


def account_spec(payment_method: str, currency: str) -> tuple[str, str]:
    method = clean(payment_method).casefold()
    if method == "cash":
        return f"Cash {currency}", "cash"
    if method == "georgian card":
        return f"Georgian Card {currency}", "debit_card"
    if method == "mono":
        return f"Mono {currency}", "debit_card"
    if method == "wise":
        return f"Wise {currency}", "checking"
    if method in {"", "unspecified"}:
        return f"Unspecified {currency}", "other"
    if method in {"другое", "other"}:
        return f"Other {currency}", "other"
    # Preserve an unexpected source value visibly rather than guessing a card
    # type or silently placing money in a different account.
    return f"{clean(payment_method)} {currency}", "other"


def convert_to_usd(amount: Decimal, currency: str, gel_to_usd: Decimal | None) -> tuple[Decimal, str]:
    """Convert GEL at an explicitly supplied fixed rate; USD stays unchanged.

    Other currencies intentionally fail rather than silently applying a GEL
    rate to EUR or UAH.
    """
    if gel_to_usd is None:
        return amount, currency
    if currency == "USD":
        return amount, "USD"
    if currency == "GEL":
        return (amount * gel_to_usd).quantize(Decimal("0.01")), "USD"
    raise ValueError(f"no fixed USD rate supplied for {currency}")


def default_accounts_for_currency(currency: str | None) -> tuple[tuple[str, str, str], ...]:
    if currency is None:
        return DEFAULT_ACCOUNTS
    return (
        (f"Cash {currency}", "cash", currency),
        (f"Georgian Card {currency}", "debit_card", currency),
        (f"Mono {currency}", "debit_card", currency),
        (f"Wise {currency}", "checking", currency),
    )


def sheet_csv_url(sheet_url: str, gid: str) -> str:
    parsed = urlparse(sheet_url)
    parts = [part for part in parsed.path.split("/") if part]
    try:
        spreadsheet_id = parts[parts.index("d") + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError("--sheet-url must be a Google Sheets URL containing /d/<spreadsheet-id>/") from exc
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?" + urlencode(
        {"format": "csv", "gid": gid}
    )


def spreadsheet_id_from_url(sheet_url: str) -> str:
    parts = [part for part in urlparse(sheet_url).path.split("/") if part]
    try:
        return parts[parts.index("d") + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError("--sheet-url must be a Google Sheets URL containing /d/<spreadsheet-id>/") from exc


def download_rows(sheet_url: str, gid: str, access_token: str | None = None) -> list[dict[str, str]]:
    return download_rows_for_headers(
        sheet_url,
        gid,
        {"Transaction ID", "Date", "Amount", "Currency", "Category", "Payment Method", "Source"},
        "Transactions",
        "Transactions!A:K",
        access_token,
    )


def download_category_names(sheet_url: str, gid: str, access_token: str | None = None) -> list[str]:
    rows = download_rows_for_headers(sheet_url, gid, {"Categories"}, "Lists", "Lists!A:D", access_token)
    return [clean(row.get("Categories")) for row in rows if clean(row.get("Categories"))]


def download_rows_for_headers(
    sheet_url: str, gid: str, required: set[str], sheet_name: str, range_: str, access_token: str | None
) -> list[dict[str, str]]:
    if access_token:
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id_from_url(sheet_url)}/values/"
            f"{quote(range_, safe='')}?valueRenderOption=UNFORMATTED_VALUE"
        )
        request = Request(url, headers={"Authorization": f"Bearer {access_token}"})
        try:
            with urlopen(request, timeout=30) as response:  # nosec B310: Google API URL is constructed above
                values = json.loads(response.read().decode()).get("values", [])
        except HTTPError as exc:
            raise ImportErrorWithContext(
                f"Google Sheets API returned HTTP {exc.code}; refresh GOOGLE_SHEETS_ACCESS_TOKEN and retry"
            ) from exc
        if not values:
            raise ImportErrorWithContext(f"{sheet_name} has no rows")
        headers = [str(value) for value in values[0]]
        if not required.issubset(set(headers)):
            raise ImportErrorWithContext(f"{sheet_name} header is invalid; got {headers!r}")
        return [
            {header: str(row[index]) if index < len(row) else "" for index, header in enumerate(headers)}
            for row in values[1:]
            if any(clean(str(value)) for value in row)
        ]
    request = Request(sheet_csv_url(sheet_url, gid), headers={"User-Agent": "Aurum Google Sheets importer"})
    with urlopen(request, timeout=30) as response:  # nosec B310: user explicitly supplies this sheet URL
        content = response.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise ImportErrorWithContext(f"{sheet_name} header is invalid; got {reader.fieldnames!r}")
    return [dict(row) for row in reader if any(clean(value) for value in row.values())]


@dataclass
class AurumClient:
    api_url: str
    username: str | None = None
    password: str | None = None

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        if self.username is not None and self.password is not None:
            token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        request = Request(f"{self.api_url.rstrip('/')}/{path.lstrip('/')}", data=body, headers=headers, method=method)
        for attempt in range(4):
            try:
                with urlopen(request, timeout=30) as response:  # nosec B310: API URL is user-supplied
                    raw = response.read().decode()
                break
            except HTTPError as exc:
                if exc.code == 429 and attempt < 3:
                    # Aurum's nginx protects the API with a request-rate
                    # limit.  A one-off import creates two requests per row;
                    # back off rather than turning a harmless retry into a
                    # partial migration failure.
                    time.sleep(1 + attempt)
                    continue
                detail = exc.read().decode(errors="replace")
                raise ImportErrorWithContext(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc
        return json.loads(raw) if raw else None

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("POST", path, payload)

    def patch(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("PATCH", path, payload)


def by_name(items: Iterable[dict[str, Any]]) -> dict[tuple[str, str | None], dict[str, Any]]:
    return {(item["name"].casefold(), item.get("currency")): item for item in items}


def import_rows(
    client: AurumClient,
    rows: list[dict[str, str]],
    category_names: Iterable[str] = (),
    dry_run: bool = False,
    gel_to_usd: Decimal | None = None,
    update_existing: bool = False,
) -> dict[str, int]:
    accounts = by_name(client.get("accounts"))
    categories = {(item["name"].casefold(), item["kind"]): item for item in client.get("categories")}
    tags = {item["name"].casefold(): item for item in client.get("tags")}

    def ensure_account(name: str, type_: str, currency: str) -> dict[str, Any]:
        key = (name.casefold(), currency)
        if key not in accounts:
            if dry_run:
                accounts[key] = {"id": -len(accounts) - 1, "name": name, "currency": currency}
            else:
                accounts[key] = client.post("accounts", {"name": name, "type": type_, "currency": currency})
        return accounts[key]

    def ensure_category(name: str) -> dict[str, Any] | None:
        if not name:
            return None
        key = (name.casefold(), "expense")
        if key not in categories:
            payload = {
                "name": name,
                "kind": "expense",
                "color": CATEGORY_COLORS[len(categories) % len(CATEGORY_COLORS)],
                "sort_order": len(categories),
            }
            categories[key] = {"id": -len(categories) - 1, **payload} if dry_run else client.post("categories", payload)
        return categories[key]

    def ensure_tag(name: str) -> dict[str, Any]:
        key = name.casefold()
        if key not in tags:
            tags[key] = {"id": -len(tags) - 1, "name": name} if dry_run else client.post("tags", {"name": name})
        return tags[key]

    # Provision the agreed personal structure even if an account/category
    # has no transactions in the initial export yet.
    for name, type_, currency in default_accounts_for_currency("USD" if gel_to_usd is not None else None):
        ensure_account(name, type_, currency)
    for name in category_names:
        ensure_category(name)

    imported = updated = skipped = unresolved_categories = 0
    for number, row in enumerate(rows, start=2):
        external_id = clean(row.get("Transaction ID"))
        if not external_id:
            raise ImportErrorWithContext(f"row {number}: Transaction ID is required for idempotent import")
        existing = client.get("transactions?" + urlencode({"external_id": external_id, "page_size": 1}))
        currency = clean(row.get("Currency")).upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ImportErrorWithContext(f"row {number}: invalid currency {currency!r}")
        try:
            amount = Decimal(clean(row.get("Amount")))
            if amount <= 0:
                raise ValueError("must be positive")
            transaction_date = parse_sheet_date(clean(row.get("Date")) or clean(row.get("Timestamp")))
            transaction_notes = notes(row)
            amount, currency = convert_to_usd(amount, currency, gel_to_usd)
        except (InvalidOperation, ValueError) as exc:
            raise ImportErrorWithContext(f"row {number}: {exc}") from exc
        account_name, account_type = account_spec(clean(row.get("Payment Method")), currency)
        account = ensure_account(account_name, account_type, currency)
        category = ensure_category(clean(row.get("Category")))
        source = clean(row.get("Source")).casefold() or "unknown"
        tag_ids = [ensure_tag(IMPORT_TAG)["id"], ensure_tag(f"{SOURCE_PREFIX}{source}")["id"]]
        payload = {
            "account_id": account["id"],
            "category_id": category["id"] if category else None,
            "type": "expense",
            "amount": f"{amount:.2f}",
            "description": short_description(row),
            "merchant": clean(row.get("Merchant")) or None,
            "notes": transaction_notes,
            "date": transaction_date,
            "tag_ids": tag_ids,
            "external_id": external_id,
        }
        if category is None:
            unresolved_categories += 1
        if existing["total"]:
            if update_existing:
                if not dry_run:
                    client.patch(f"transactions/{existing['items'][0]['id']}", payload)
                updated += 1
            else:
                skipped += 1
            continue
        if not dry_run:
            client.post("transactions", payload)
        imported += 1
    return {
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "unresolved_categories": unresolved_categories,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet-url", required=True, help="Google Sheet URL")
    parser.add_argument("--api-url", default="http://localhost:3000/api", help="Aurum API base URL")
    parser.add_argument("--gid", default=DEFAULT_TRANSACTIONS_GID, help="Transactions tab gid")
    parser.add_argument("--lists-gid", default=DEFAULT_LISTS_GID, help="Lists tab gid")
    parser.add_argument(
        "--google-access-token",
        default=os.environ.get("GOOGLE_SHEETS_ACCESS_TOKEN"),
        help="OAuth access token; defaults to GOOGLE_SHEETS_ACCESS_TOKEN",
    )
    parser.add_argument("--username", help="Aurum HTTP Basic Auth username")
    parser.add_argument("--password", help="Aurum HTTP Basic Auth password")
    parser.add_argument("--gel-to-usd", type=Decimal, help="Fixed conversion rate: USD for one GEL")
    parser.add_argument("--update-existing", action="store_true", help="Update already imported external IDs")
    parser.add_argument("--dry-run", action="store_true", help="Validate mappings without writing to Aurum")
    args = parser.parse_args()
    if (args.username is None) != (args.password is None):
        parser.error("--username and --password must be provided together")
    try:
        rows = download_rows(args.sheet_url, args.gid, args.google_access_token)
        categories = download_category_names(args.sheet_url, args.lists_gid, args.google_access_token)
        summary = import_rows(
            AurumClient(args.api_url, args.username, args.password),
            rows,
            categories,
            args.dry_run,
            args.gel_to_usd,
            args.update_existing,
        )
    except (ImportErrorWithContext, OSError, ValueError) as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1
    mode = "Dry run" if args.dry_run else "Import"
    print(f"{mode} complete: {summary['imported']} imported, {summary['updated']} updated, {summary['skipped']} already present, "
          f"{summary['unresolved_categories']} without a category.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
