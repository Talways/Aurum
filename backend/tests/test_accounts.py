"""Account-type behavior shared by the accounts API and financial summaries."""
from datetime import date

from httpx import AsyncClient

from tests.helpers import money, txn_payload


async def test_debit_card_is_a_liquid_account_type(client: AsyncClient, categories):
    response = await client.post(
        "/accounts",
        json={"name": "Debit card", "type": "debit_card", "currency": "RUB"},
    )

    assert response.status_code == 201
    debit_card = response.json()
    assert debit_card["type"] == "debit_card"

    transaction = await client.post(
        "/transactions",
        json=txn_payload(
            debit_card["id"],
            type="income",
            amount="1000.00",
            category_id=categories["Salary"]["id"],
            date=date.today().isoformat(),
        ),
    )
    assert transaction.status_code == 201

    summary = await client.get("/net-worth/summary", params={"range": "all"})

    assert summary.status_code == 200
    assert money(summary.json()["current"]) == money("1000.00")


async def test_setting_actual_balance_keeps_ledger_and_net_worth_consistent(client: AsyncClient, categories):
    account_response = await client.post(
        "/accounts",
        json={"name": "Wise", "type": "checking", "currency": "USD"},
    )
    account = account_response.json()

    transaction = await client.post(
        "/transactions",
        json=txn_payload(
            account["id"],
            type="income",
            amount="1000.00",
            category_id=categories["Salary"]["id"],
            date=date.today().isoformat(),
        ),
    )
    assert transaction.status_code == 201

    response = await client.put(f"/accounts/{account['id']}/balance", json={"balance": "333.30"})

    assert response.status_code == 200
    assert money(response.json()["balance"]) == money("333.30")

    accounts = await client.get("/accounts")
    account_after_reconciliation = next(row for row in accounts.json() if row["id"] == account["id"])
    assert money(account_after_reconciliation["balance"]) == money("333.30")

    summary = await client.get("/net-worth/summary", params={"range": "all"})
    assert summary.status_code == 200
    assert money(summary.json()["current"]) == money("333.30")
