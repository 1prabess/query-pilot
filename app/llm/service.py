import logging
from typing import Any

from app.core.config import get_settings
from app.core.errors import LLMError, LLMUnavailable
from app.llm.base import TRUNCATED_REASONS, Completion, Provider, explain
from app.llm.context import budget_context
from app.llm.gemini import Gemini
from app.llm.ollama import Ollama
from app.llm.parse import _strip_fence, parse_json_response, recover_sql
from app.llm.prompts import build_system_prompt, build_user_prompt

logger = logging.getLogger(__name__)

# Room inside a local model's context window for the system prompt and the
# question, and for the answer it has to write back.
_PROMPT_RESERVE = 400
_OUTPUT_RESERVE = 400


class LLMService:
    def __init__(self, providers: list[Provider] | None = None) -> None:
        self.settings = get_settings()
        self._providers = providers or self._select_providers()
        self._provider = next(
            (p for p in self._providers if p.is_configured()), None)

    def _select_providers(self) -> list[Provider]:
        """The providers to try, in the order the setting says to try them.

        `AI_PROVIDER` picks one outright, so a machine that has both a Gemini
        key and a local Ollama does not have to unset the key to switch
        between them. `auto` prefers Gemini and falls back to Ollama.
        """
        choice = self.settings.AI_PROVIDER
        if choice == "gemini":
            return [Gemini(self.settings)]
        if choice == "ollama":
            return [Ollama(self.settings)]
        return [Gemini(self.settings), Ollama(self.settings)]

    def _unavailable_message(self) -> str:
        """Say what is missing, rather than only that something is."""
        choice = self.settings.AI_PROVIDER
        if choice == "gemini":
            return (
                "AI_PROVIDER is set to gemini, but GEMINI_API_KEY is not set in "
                ".env. Write the SQL by hand, or set AI_PROVIDER to ollama."
            )
        if choice == "ollama":
            return (
                "AI_PROVIDER is set to ollama, but OLLAMA_BASE_URL and "
                "OLLAMA_MODEL are not set in .env. Write the SQL by hand, or "
                "set AI_PROVIDER to gemini."
            )
        return (
            "No AI provider is configured. Set GEMINI_API_KEY, or the Ollama "
            "settings, in .env -- or write the SQL by hand."
        )

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

    def _budgets(self) -> tuple[int, int]:
        """(schema tokens, output tokens) that fit the selected provider.

        Gemini has a context window of a million tokens and the budget is
        never the binding constraint. A local model runs with a window of a
        few thousand, and a prompt that does not fit in the window is a
        prompt the model cannot read -- so the schema is trimmed to fit (the
        model is told it was trimmed) and the answer gets what is left.
        """
        schema_budget = self.settings.AI_SCHEMA_TOKEN_BUDGET
        output_budget = self.settings.AI_MAX_OUTPUT_TOKENS

        if self._provider is not None and self._provider.name == "ollama":
            window = self.settings.OLLAMA_NUM_CTX
            schema_budget = min(
                schema_budget,
                max(512, window - _PROMPT_RESERVE - _OUTPUT_RESERVE),
            )
            output_budget = min(
                output_budget,
                max(256, window - schema_budget - _PROMPT_RESERVE),
            )

        return schema_budget, output_budget

    async def generate(
        self,
        *,
        question: str,
        schema_doc: dict,
        writes_enabled: bool = False,
        max_output_tokens: int | None = None,
    ) -> Completion:
        if self._provider is None:
            raise LLMUnavailable(self._unavailable_message())

        schema_budget, output_budget = self._budgets()
        schema_text, truncated = budget_context(
            schema_doc, max_tokens=schema_budget
        )
        prompt = build_user_prompt(
            question, schema_text, truncated=truncated, writes_enabled=writes_enabled
        )
        budget = max_output_tokens or output_budget

        try:
            completion = await self._provider.complete(
                build_system_prompt(writes_enabled=writes_enabled),
                prompt,
                max_output_tokens=budget,
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
