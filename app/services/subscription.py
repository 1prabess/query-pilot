"""The subscription: which plan a user is on, and how that changes.

Reading a subscription is a lookup. Changing one is a decision, and today
only an administrator may make it -- which is what the paid period of this
product looks like before there is a payment provider.

The seam is deliberately narrow: `set_plan` takes a tier and a status, and
nothing else. When a payment provider is connected, its webhook calls the
same method with the same arguments, and the administrator route becomes a
second way to do the same thing rather than the only way. Nothing else in
the product has to change to make that happen.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnknownSubscriptionStatus, UnknownTier
from app.models.subscription import Subscription
from app.services.plans import (
    ACTIVE,
    FREE,
    SUBSCRIPTION_STATUSES,
    is_known_tier,
)


class SubscriptionService:
    """Reads and changes the subscription row.

    The session arrives in the constructor and every method uses it. Nothing
    here commits; the caller owns the unit of work.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for(self, user_id: uuid.UUID) -> Subscription:
        """The subscription row, creating the free one if it is missing.

        A user who registered before this table existed has no row, and
        their account has to work anyway. Rather than a migration that every
        future user would have to repeat, the row is made on first sight.
        """
        subscription = await self.session.scalar(
            select(Subscription).where(Subscription.user_id == user_id)
        )
        if subscription is None:
            subscription = Subscription(user_id=user_id, tier=FREE, status=ACTIVE)
            self.session.add(subscription)
            await self.session.flush()
        return subscription

    async def set_plan(
        self,
        user_id: uuid.UUID,
        *,
        tier: str,
        status: str = ACTIVE,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        provider_customer_id: str | None = None,
        provider_subscription_id: str | None = None,
    ) -> Subscription:
        """Put a user on a plan.

        The tier name is checked rather than trusted. A typo in a payment
        provider's webhook must not become a plan nobody defined.
        """
        normalized = tier.strip().lower()
        if not is_known_tier(normalized):
            raise UnknownTier(f"There is no plan called '{tier}'.")

        normalized_status = status.strip().lower()
        if normalized_status not in SUBSCRIPTION_STATUSES:
            raise UnknownSubscriptionStatus(
                f"There is no subscription status called '{status}'."
            )

        subscription = await self.get_for(user_id)
        subscription.tier = normalized
        subscription.status = normalized_status
        subscription.period_start = period_start
        subscription.period_end = period_end
        subscription.provider_customer_id = provider_customer_id
        subscription.provider_subscription_id = provider_subscription_id

        await self.session.flush()
        return subscription
