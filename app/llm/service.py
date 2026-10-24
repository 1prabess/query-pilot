import logging
from typing import Any

from app.core.config import get_settings
from app.core.errors import LLMError, LLMUnavailable
from app.llm.base import TRUNCATED_REASONS, Completion, Provider, explain
from app.llm.context import budget_context
from app.llm.gemini import Gemini
from app.llm.ollama import Ollama
from app.llm.parse import _strip_fence, parse_json_response, recover_sql
from app.llm.prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self, providers: list[Provider] | None = None) -> None:
        self.settings = get_settings()
        self._providers = providers or [
            Gemini(self.settings), Ollama(self.settings)]
        self._provider = next(
            (p for p in self._providers if p.is_configured()), None)

    def _parse(self, text: str) -> dict[str, Any]:
        parsed = parse_json_response(text)
        if not isinstance(parsed, dict):
            raise LLMError(
                "The AI returned a reply that was not in the expected JSON format. "
                "Try asking the question again, or write the SQL by hand."
            )

        sql = (parsed.get("sql") or "").strip()
        if not sql:
            raise LLMError(
                "The AI did not return a query. Try rephrasing the question.")

        return {
            "sql": sql.rstrip(";").strip(),
            "explanation": (parsed.get("explanation") or "").strip(),
            "assumptions": (parsed.get("assumptions") or "").strip(),
            # Every ORM goes through _strip_fence: asked for JSON, a model will
            # still fence code *inside* the JSON string.
            "orm_sqlalchemy": _strip_fence(parsed.get("orm_sqlalchemy")),
            "orm_typeorm": _strip_fence(parsed.get("orm_typeorm")),
            "orm_prisma": _strip_fence(parsed.get("orm_prisma")),
        }

    def parse_result(
        self, text: str, *, finish_reason: str | None = None
    ) -> dict[str, Any]:
        """Public because chapter 06's `QueryService` calls it.

        The fence-stripping and the JSON validation both live here, and there is
        exactly one place they should live. A second caller that re-parses the
        raw text is a second caller that will eventually forget `_strip_fence`.

        `finish_reason` is what turns an unparseable reply from a failure into a
        partial success. A reply cut off by the token cap is not malformed, it
        is short -- and because `sql` is the first key, the statement usually
        survived and the ORM blocks did not.
        """
        try:
            return self._parse(text)
        except LLMError:
            if finish_reason not in TRUNCATED_REASONS:
                raise
        recovered = recover_sql(text)
        if recovered is None:
            raise LLMError(
                "The AI ran out of room before it finished answering. "
                "Try a narrower question, or write the SQL by hand."
            )
        logger.warning(
            "AI reply truncated (%s); recovered the SQL, ORM tabs are empty",
            finish_reason,
        )
        return {
            "sql": recovered.rstrip(";").strip(),
            "explanation": "",
            "assumptions": "",
            "orm_sqlalchemy": "",
            "orm_typeorm": "",
            "orm_prisma": "",
        }

    async def generate(
        self, *, question: str, schema_doc: dict, max_output_tokens: int | None = None
    ) -> Completion:
        if self._provider is None:
            raise LLMUnavailable(
                "No AI provider is configured. Add a free key to .env, or write "
                "the SQL by hand -- the rest of QueryPilot works without one."
            )

        schema_text, truncated = budget_context(
            schema_doc, max_tokens=self.settings.AI_SCHEMA_TOKEN_BUDGET
        )
        prompt = build_user_prompt(question, schema_text, truncated=truncated)
        budget = max_output_tokens or self.settings.AI_MAX_OUTPUT_TOKENS

        try:
            completion = await self._provider.complete(
                SYSTEM_PROMPT, prompt, max_output_tokens=budget
            )
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(explain(exc)) from exc

        return completion


_llm_service: LLMService | None = None


def get_llm_service() -> LLMService:
    """One instance for the process.

    Built on first use rather than at import, because `LLMService.__init__`
    reads settings, and settings are not readable at import time in every
    deployment (a secret mounted after the process starts, a test that sets
    `GEMINI_API_KEY` in a fixture).

    Chapter 06's `QueryService` takes this as a parameter rather than importing
    it, so the whole orchestration can be tested with a fake LLM and no network.
    """
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
