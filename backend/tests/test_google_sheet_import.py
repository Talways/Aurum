from scripts.import_google_sheet import import_rows, notes, parse_sheet_date, short_description


def test_parse_sheet_date_converts_google_serial_to_calendar_day():
    # Google Sheets reports 2026-09-28 as serial 46293 in the supplied workbook.
    assert parse_sheet_date("46293") == "2026-09-28"
    assert parse_sheet_date("2026-09-28") == "2026-09-28"


def test_receipt_mapping_keeps_short_display_text_and_full_ocr_in_notes():
    row = {
        "Description": "Receipt OCR\n" + "x" * 300,
        "Merchant": "Carrefour",
        "Category": "Продукты",
        "Source": "receipt",
        "Receipt URL": "https://example.test/receipt",
    }
    assert short_description(row) == "Carrefour"
    assert notes(row) == row["Description"] + "\n\nReceipt URL: https://example.test/receipt"


class FakeClient:
    def __init__(self):
        self.accounts = []
        self.categories = []
        self.tags = []
        self.transactions = []

    def get(self, path):
        if path == "accounts":
            return self.accounts
        if path == "categories":
            return self.categories
        if path == "tags":
            return self.tags
        if path.startswith("transactions?"):
            external_id = path.split("external_id=", 1)[1].split("&", 1)[0]
            return {"total": sum(tx["external_id"] == external_id for tx in self.transactions)}
        raise AssertionError(path)

    def post(self, path, payload):
        collection = {"accounts": self.accounts, "categories": self.categories, "tags": self.tags}.get(path)
        if collection is not None:
            item = {"id": len(collection) + 1, **payload}
            collection.append(item)
            return item
        if path == "transactions":
            self.transactions.append(payload)
            return payload
        raise AssertionError(path)


def test_import_is_idempotent_and_creates_source_tags():
    client = FakeClient()
    rows = [{
        "Transaction ID": "google-row-1",
        "Timestamp": "46293",
        "Date": "46293",
        "Amount": "24.97",
        "Currency": "GEL",
        "Category": "Продукты",
        "Merchant": "Carrefour",
        "Description": "weekly shop",
        "Payment Method": "",
        "Source": "receipt",
        "Receipt URL": "",
    }]
    first = import_rows(client, rows, ["Продукты", "Здоровье"])
    second = import_rows(client, rows, ["Продукты", "Здоровье"])

    assert first == {"imported": 1, "skipped": 0, "unresolved_categories": 0}
    assert second == {"imported": 0, "skipped": 1, "unresolved_categories": 0}
    assert len(client.transactions) == 1
    assert client.transactions[0]["date"] == "2026-09-28"
    assert client.transactions[0]["external_id"] == "google-row-1"
    assert {account["name"] for account in client.accounts} >= {
        "Cash GEL", "Georgian Card GEL", "Mono UAH", "Wise USD", "Unspecified GEL"
    }
    assert {tag["name"] for tag in client.tags} == {"import:google-sheets", "source:receipt"}
