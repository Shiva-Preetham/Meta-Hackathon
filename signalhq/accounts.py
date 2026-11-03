"""Simulated accounts: a deterministic plan tier and MRR for each customer id.

Tweets carry no revenue, so revenue is simulated and labelled as such everywhere. The same
``external_ref`` and seed always produce the same plan, so rankings are reproducible.
Distribution (``config.PLAN_TIERS``): 55% free, 25% individual ($10), 12% family ($17),
8% business ($12 per seat, 2 to 250 seats, log-uniform).
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass

from signalhq.config import BUSINESS_SEATS_RANGE, PLAN_TIERS


@dataclass(frozen=True)
class SimulatedAccount:
    external_ref: str
    plan: str
    mrr_cents: int
    seats: int


def _rng_for(external_ref: str, seed: int) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{external_ref}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def simulate_account(external_ref: str, seed: int = 42) -> SimulatedAccount:
    rng = _rng_for(external_ref, seed)
    draw = rng.random()
    cumulative = 0.0
    plan, price = PLAN_TIERS[-1][0], PLAN_TIERS[-1][2]
    for name, probability, cents in PLAN_TIERS:
        cumulative += probability
        if draw < cumulative:
            plan, price = name, cents
            break
    seats = 1
    if plan == "business":
        lo, hi = BUSINESS_SEATS_RANGE
        seats = int(round(math.exp(rng.uniform(math.log(lo), math.log(hi)))))
        seats = max(lo, min(hi, seats))
    return SimulatedAccount(external_ref=external_ref, plan=plan, mrr_cents=price * seats, seats=seats)
