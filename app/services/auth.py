import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import hash_password
from app.models.magic_link import MagicLink
from app.models.refresh_token import RefreshToken
from app.models.subscription import Subscription
from app.models.user import User
from app.services.plans import ACTIVE, FREE


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    user: User


class AuthError(AppError, status_code=401, code="auth_failed"):
    pass


class EmailTaken(AppError, status_code=409, code="email_taken"):
    pass


class AuthService:
    """Everything that changes or reads authentication state.

    The session arrives in the constructor and every method uses it. Nothing in
    this class opens its own session and nothing outside this class is allowed
    to write a `RefreshToken` — that is the whole reason it exists.

    One instance per request, built by `get_auth_service`. Not a singleton: it
    holds a session, and a session does not outlive a request.

    Methods do not commit. The caller owns the unit of work, so a route can
    compose several calls into one transaction. The single exception is the
    reuse-detection branch in `refresh`, which has to commit a revocation that
    must outlive the rollback caused by the exception it is about to raise.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def register_user(self, email: str, password: str) -> TokenPair:
        normalized = email.strip().lower()
        existing = await self.session.scalar(
            select(User).where(User.email == normalized)
        )
        if existing is not None:
            raise EmailTaken("An account with that email already exists.")

        user = User(
            email=normalized,
            password_hash=await hash_password(password),
        )
        self.session.add(user)
        # flush, not commit: the INSERT needs a row before `_issue` can build a
        # RefreshToken with a real user_id. Committing here would end the unit of
        # work two statements early.
        await self.session.flush()

        # Everybody starts on the free plan. The row is written here rather
        # than left to be made on first sight so that "which plan is this
        # customer on" has one answer for every account, old and new.
        self.session.add(
            Subscription(user_id=user.id, tier=FREE, status=ACTIVE)
        )

        return await self._issue(user, family_id=uuid.uuid4())

    async def authenticate(self, email: str, password: str) -> TokenPair:
        user = await self.session.scalar(
            select(User).where(User.email == email.strip().lower())
        )

        # Unconditional, passing None when there is no account so verify_password
        # falls back to its dummy hash. One condition below, not two: whether the
        # address is unknown or the password is wrong, the caller gets the same
        # exception having paid the same ~33ms of Argon2, so response time cannot
        # be used to enumerate accounts.
        verified = await security.verify_password(
            user.password_hash if user else None, password
        )

        if user is None or not verified or not user.is_active:
            raise AuthError("Invalid email or password.")

        return await self._issue(user, family_id=uuid.uuid4())

    async def refresh(self, raw_token: str) -> TokenPair:
        digest = security.hash_token(raw_token)
        row = await self.session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == digest)
        )

        if row is None:
            raise AuthError("Invalid or expired session.")

        if row.revoked_at is not None:
            # A revoked token came back. Either the attacker is replaying, or the
            # legitimate user is on a second device whose token we retired. Both
            # are indistinguishable here and both mean the family is suspect.
            await self._revoke_family(row.family_id)
            # get_db rolls back when the AuthError propagates, which would discard
            # the revocation above, so commit it before raising.
            await self.session.commit()
            raise AuthError("Session revoked. Please sign in again.")

        if row.expires_at < datetime.now(UTC):
            raise AuthError("Invalid or expired session.")

        user = await self.session.get(User, row.user_id)
        if user is None or not user.is_active:
            raise AuthError("Invalid or expired session.")

        row.revoked_at = datetime.now(UTC)
        return await self._issue(user, family_id=row.family_id)

    async def logout(self, raw_token: str) -> None:
        row = await self.session.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash == security.hash_token(raw_token)
            )
        )
        if row is not None:
            await self._revoke_family(row.family_id)

    async def request_magic_link(self, email: str) -> tuple[User, str] | None:
        """Create a link, or nothing if the address is unknown.

        Returns the user and the *raw* token rather than sending anything. Two
        reasons, and neither of them is testability:

        - Sending email is blocking I/O. Doing it inline here holds the event
          loop for the duration of a network round trip to an SMTP provider, on
          a request an attacker can fire a thousand of a minute.
        - The caller decides what "delivered" means. This method makes a token;
          the route logs it in development, a real mailer queues it in
          production, and neither has to stub an email client to find out whether
          the row was written.

        `None` for an unknown address is a real answer, not a failure.
        """
        user = await self.session.scalar(
            select(User).where(User.email == email.strip().lower())
        )
        if user is None:
            return None

        raw = security.new_refresh_token()
        self.session.add(
            MagicLink(
                user_id=user.id,
                token_hash=security.hash_token(raw),
                expires_at=(
                    datetime.now(UTC)
                    + timedelta(minutes=get_settings().MAGIC_LINK_TTL_MINUTES)
                ),
            )
        )
        return user, raw

    async def consume_magic_link(self, raw: str) -> User:
        """Spend a link, atomically, and return the user it belonged to.

        The UPDATE carries the "not already spent and not expired" test, so two
        simultaneous clicks race in the database rather than in Python: exactly
        one gets a row back and the loser sees None. Reading first and writing
        second would let both succeed.
        """
        digest = security.hash_token(raw)
        row = (
            await self.session.execute(
                update(MagicLink)
                .where(
                    MagicLink.token_hash == digest,
                    MagicLink.consumed_at.is_(None),
                    MagicLink.expires_at > datetime.now(UTC),
                )
                .values(consumed_at=datetime.now(UTC))
                .returning(MagicLink.user_id)
            )
        ).scalar_one_or_none()

        if row is None:
            raise AuthError("That link is invalid or has already been used.")

        user = await self.session.get(User, row)
        if user is None or not user.is_active:
            raise AuthError("That link is invalid or has already been used.")

        # Proof of control of the inbox, which is what this column means.
        user.email_verified = True
        return user

    async def issue_for(self, user: User) -> TokenPair:
        """Mint a session for a user who has already proven who they are.

        The magic-link path. Public because a route calls it; `_issue` is
        private because only methods of this class should reach it.
        """
        return await self._issue(user, family_id=uuid.uuid4())

    async def _issue(self, user: User, *, family_id: uuid.UUID) -> TokenPair:
        settings = get_settings()
        raw_refresh = security.new_refresh_token()
        self.session.add(
            RefreshToken(
                user_id=user.id,
                token_hash=security.hash_token(raw_refresh),
                family_id=family_id,
                expires_at=(
                    datetime.now(UTC)
                    + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)
                ),
            )
        )
        access_token, expires_at = security.create_access_token(str(user.id))
        return TokenPair(
            access_token=access_token,
            refresh_token=raw_refresh,
            expires_in=int(
                (expires_at - datetime.now(UTC)).total_seconds()
            ),
            user=user,
        )

    async def _revoke_family(self, family_id: uuid.UUID) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )