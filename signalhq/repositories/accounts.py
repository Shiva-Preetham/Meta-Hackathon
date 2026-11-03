from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from signalhq.accounts import simulate_account
from signalhq.models import Account


class AccountRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def get_or_create(self, external_ref: str, seed: int) -> int:
        """Return the account id, creating a simulated account on first sight."""
        sim = simulate_account(external_ref, seed)
        self.s.execute(
            insert(Account)
            .values(
                external_ref=external_ref,
                plan=sim.plan,
                mrr_cents=sim.mrr_cents,
                simulated=True,
            )
            .on_conflict_do_nothing(index_elements=["external_ref"])
        )
        return self.s.execute(
            select(Account.id).where(Account.external_ref == external_ref)
        ).scalar_one()

    def get(self, account_id: int) -> Account | None:
        return self.s.get(Account, account_id)
