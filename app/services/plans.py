"""The plans, and the numbers behind them.

A plan is a row of numbers, not a branch in the code. Every limit the
product has lives in this table, so "what may this user do" is one lookup
rather than a scatter of `if tier ==` checks spread across the services.

The plans are code rather than rows in a table on purpose. A plan is part
of what the product *is* -- changing one is a release, not an edit someone
makes in a database. What lives in the database is which plan a user is on.
"""

from __future__ import annotations

from dataclasses import dataclass

FREE = "free"
BASIC = "basic"
PREMIUM = "premium"

# A subscription is in one of these states. Only `active` grants the plan it
# names; the other two fall back to the free plan, which is what a customer
# whose payment did not go through should get rather than a cut-off account.
ACTIVE = "active"
CANCELED = "canceled"
PAST_DUE = "past_due"

SUBSCRIPTION_STATUSES = (ACTIVE, CANCELED, PAST_DUE)


@dataclass(frozen=True)
class Plan:
    """What one tier allows.

    `max_rows` is the most rows one answer may carry. `execution_timeout_ms`
    is how long a query may run against the customer's database; the
    deployment's own setting is a ceiling on it, so an operator can hold
    every tier down without editing this table.
    """

    tier: str
    label: str
    max_projects: int
    # `None` means no limit. A limit of zero would be a plan that allows
    # nothing, which is not a thing anyone pays for.
    monthly_queries: int | None
    max_rows: int
    execution_timeout_ms: int
    allows_writes: bool


FREE_PLAN = Plan(
    tier=FREE,
    label="Free",
    max_projects=1,
    monthly_queries=50,
    max_rows=100,
    execution_timeout_ms=5_000,
    allows_writes=False,
)

BASIC_PLAN = Plan(
    tier=BASIC,
    label="Basic",
    max_projects=5,
    monthly_queries=1_000,
    max_rows=1_000,
    execution_timeout_ms=15_000,
    allows_writes=True,
)

PREMIUM_PLAN = Plan(
    tier=PREMIUM,
    label="Premium",
    max_projects=25,
    monthly_queries=None,
    max_rows=5_000,
    execution_timeout_ms=60_000,
    allows_writes=True,
)

PLANS: dict[str, Plan] = {
    plan.tier: plan for plan in (FREE_PLAN, BASIC_PLAN, PREMIUM_PLAN)
}


def get_plan(tier: str | None) -> Plan:
    """The plan for a tier name.

    An unknown tier is the free plan rather than an error: a subscription
    row that says something we do not recognise must not hand out premium
    limits by accident.
    """
    if not tier:
        return FREE_PLAN
    return PLANS.get(tier.strip().lower(), FREE_PLAN)


def is_known_tier(tier: str) -> bool:
    return tier.strip().lower() in PLANS
