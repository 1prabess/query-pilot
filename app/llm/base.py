from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Completion:
    """A provider's answer, in our terms.

    Token counts are part of the result, not an afterthought: chapter 09 bills
    with them. A provider that does not report usage should report zeroes --
    an absent field is a `None` three layers down that somebody will guess at.
    """

    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str | None = None


class Provider(Protocol):
    """What a provider has to be able to do.

    A `Protocol` rather than an abstract base class: a provider does not need
    to inherit anything to satisfy this, so a new provider is one class with
    two methods and no import gymnastics.
    """

    name: str

    async def complete(
        self, system: str, user: str, *, max_output_tokens: int
    ) -> Completion:
        ...

    def is_configured(self) -> bool: ...


def explain(exc: Exception) -> str:
    """Turn a provider's exception into a sentence a user can act on.

    The raw exception names an httpx class and a provider URL, which says
    nothing about what to do next. Rate limits are the most common failure --
    this product runs on the free tier -- so they get their own message.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 429:
        return ("the free tier's per-minute rate limit was reached -- "
                "wait a few seconds and ask again")
    if status == 503:
        return ("the free tier is saturated right now -- the provider says "
                "'high demand'. This is not a bug in your code. Wait a "
                "moment and ask again.")
    if status == 403:
        return "the AI provider rejected the key. Check that it is still valid."
    if status == 400:
        return "the AI provider rejected the request. Check the model name."
    if status == 404:
        return ("that model is not available on your key's tier. "
                "Change GEMINI_MODEL in .env to one that is.")
    return f"the AI provider could not be reached ({type(exc).__name__})"


TRUNCATED_REASONS = frozenset({"MAX_TOKENS", "length"})
