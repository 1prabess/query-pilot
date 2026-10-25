class AppError(Exception):
    """Base for every failure the API reports rather than crashes on.

    A subclass declares its status and its machine-readable code *as a class
    attribute*, so the handler never has to know which types exist.
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code

    def __init_subclass__(cls, status_code: int, code: str, **kw):
        super().__init_subclass__(**kw)
        cls.status_code = status_code
        cls.code = code


class NoConnection(AppError, status_code=422, code="no_connection"):
    pass


class DecryptionError(AppError, status_code=500, code="credential_undecryptable"):
    pass


class SchemaTooLarge(AppError, status_code=413, code="schema_too_large"):
    """The uploaded dump is over the size limit."""


class SchemaParseFailed(AppError, status_code=422, code="schema_parse_failed"):
    """The upload was not a dump we can read as a schema."""


class NoSchemaTables(AppError, status_code=422, code="no_schema_tables"):
    """The upload parsed cleanly but held no tables we can use."""


class NoSchemaStored(AppError, status_code=404, code="no_schema_stored"):
    """This project has no schema document yet."""


class CustomerDatabaseUnreachable(
    AppError, status_code=502, code="customer_database_unreachable"
):
    """We could not reach the customer's database to read its schema."""


class LLMError(AppError, status_code=502, code="provider_error"):
    """The provider was reachable and the answer was not usable."""


class LLMUnavailable(LLMError, status_code=503, code="no_provider"):
    """There is no provider at all, as opposed to one that just failed."""


class QueryNotFound(AppError, status_code=404, code="query_not_found"):
    """No query with that id belongs to this project."""


class QueryExecutionFailed(AppError, status_code=502, code="query_execution_failed"):
    """The customer's database refused the query."""


class QueryTimeout(AppError, status_code=504, code="query_timeout"):
    """The query ran for longer than we let it."""


class WritesDisabled(AppError, status_code=422, code="writes_disabled"):
    """This project is not allowed to run queries that change data."""


class WriteNotConfirmed(AppError, status_code=422, code="write_not_confirmed"):
    """The query changes data, and the caller did not say it meant it."""


class UnsafeQuery(AppError, status_code=422, code="unsafe_query"):
    """The answer was not a single read-only statement.

    The prompt asks for one SELECT and the model nearly always complies. The
    promise is enforced rather than trusted, because the customer's database
    is read-only as far as this product is concerned, and that has to hold
    even when the model does not.
    """


class QuotaExceeded(AppError, status_code=429, code="quota_exceeded"):
    """The user's plan does not allow any more of this this period.

    429 because the same request will work again once the period rolls over.
    """


class ProjectLimitReached(AppError, status_code=403, code="project_limit_reached"):
    """The user already has as many projects as their plan allows."""


class PlanLimitExceeded(AppError, status_code=403, code="plan_limit_exceeded"):
    """The request asks for more than the plan allows."""


class PlanNotAllowed(AppError, status_code=403, code="plan_not_allowed"):
    """The plan does not include this capability."""


class UnknownTier(AppError, status_code=422, code="unknown_tier"):
    """No plan has that name."""


class UnknownSubscriptionStatus(AppError, status_code=422, code="unknown_status"):
    """No subscription status has that name."""

