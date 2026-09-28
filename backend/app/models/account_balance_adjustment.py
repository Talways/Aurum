"""A dated reconciliation offset for an account's calculated balance."""
from datetime import date as date_

from sqlalchemy import Date, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AccountBalanceAdjustment(Base):
    """One editable "set current balance" offset per account.

    Transaction history stays immutable; the adjustment is added to its
    calculated balance from ``as_of_date`` onward, so account and net-worth
    totals remain derived from ledger data rather than a second balance field.
    """

    __tablename__ = "account_balance_adjustments"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, unique=True)
    amount: Mapped[Numeric] = mapped_column(Numeric(14, 2), nullable=False)
    as_of_date: Mapped[date_] = mapped_column(Date, nullable=False)
