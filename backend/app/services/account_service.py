"""Account CRUD, plus each account's live balance — summed from its
Transaction rows (income adds, expense subtracts, a transfer moves the
amount from the source account to the destination account) rather than
stored, the same "derive it, don't duplicate it" approach
net_worth_service.py uses for Cash.
"""
from collections import defaultdict
from datetime import date as date_
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.account_balance_adjustment import AccountBalanceAdjustment
from app.models.enums import TransactionType
from app.models.transaction import Transaction
from app.schemas.account import AccountBalanceSet, AccountCreate, AccountUpdate, AccountWithBalance


async def _transaction_balances(session: AsyncSession) -> dict[int, Decimal]:
    result = await session.execute(
        select(Transaction.type, Transaction.amount, Transaction.account_id, Transaction.transfer_account_id)
    )
    balances: dict[int, Decimal] = defaultdict(Decimal)
    for tx_type, amount, account_id, transfer_account_id in result.all():
        if tx_type == TransactionType.INCOME:
            balances[account_id] += amount
        elif tx_type == TransactionType.EXPENSE:
            balances[account_id] -= amount
        elif tx_type == TransactionType.TRANSFER:
            balances[account_id] -= amount
            if transfer_account_id is not None:
                balances[transfer_account_id] += amount
    return balances


async def _account_balances(session: AsyncSession) -> dict[int, Decimal]:
    balances = await _transaction_balances(session)
    adjustments = await session.execute(select(AccountBalanceAdjustment.account_id, AccountBalanceAdjustment.amount))
    for account_id, amount in adjustments.all():
        balances[account_id] += amount
    return balances


def _to_read(account: Account, balance: Decimal) -> AccountWithBalance:
    return AccountWithBalance(
        id=account.id,
        name=account.name,
        type=account.type,
        currency=account.currency,
        color=account.color,
        is_archived=account.is_archived,
        balance=balance,
    )


async def list_accounts(session: AsyncSession, include_archived: bool) -> list[AccountWithBalance]:
    stmt = select(Account).order_by(Account.name)
    if not include_archived:
        stmt = stmt.where(Account.is_archived.is_(False))
    accounts = (await session.execute(stmt)).scalars().all()
    balances = await _account_balances(session)
    return [_to_read(account, balances.get(account.id, Decimal("0"))) for account in accounts]


async def create_account(session: AsyncSession, payload: AccountCreate) -> AccountWithBalance:
    account = Account(**payload.model_dump())
    session.add(account)
    await session.commit()
    await session.refresh(account)
    # A brand-new account has no transactions yet — no need to query.
    return _to_read(account, Decimal("0"))


async def update_account(session: AsyncSession, account_id: int, payload: AccountUpdate) -> AccountWithBalance:
    account = await session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(account, field, value)
    await session.commit()
    await session.refresh(account)
    balances = await _account_balances(session)
    return _to_read(account, balances.get(account.id, Decimal("0")))


async def set_account_balance(
    session: AsyncSession, account_id: int, payload: AccountBalanceSet
) -> AccountWithBalance:
    account = await session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    transaction_balance = (await _transaction_balances(session)).get(account_id, Decimal("0"))
    adjustment = await session.scalar(
        select(AccountBalanceAdjustment).where(AccountBalanceAdjustment.account_id == account_id)
    )
    if adjustment is None:
        adjustment = AccountBalanceAdjustment(account_id=account_id, amount=Decimal("0"), as_of_date=date_.today())
        session.add(adjustment)
    adjustment.amount = payload.balance - transaction_balance
    adjustment.as_of_date = payload.as_of_date or date_.today()
    await session.commit()
    await session.refresh(account)
    return _to_read(account, payload.balance)


async def delete_account(session: AsyncSession, account_id: int) -> None:
    account = await session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    await session.delete(account)
    await session.commit()
