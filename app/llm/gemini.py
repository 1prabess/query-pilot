import httpx

from app.core.errors import LLMError, LLMUnavailable
from app.llm.base import Completion

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# The provider's own words for "I will not answer this".
_BLOCKED_REASONS = frozenset(
    {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST"}
)


class Gemini:
    name = "gemini"

    def __init__(self, settings) -> None:
        self._settings = settings

    def is_configured(self) -> bool:
        return bool(self._settings.GEMINI_API_KEY.get_secret_value())

    async def complete(
        self, system: str, user: str, *, max_output_tokens: int
    ) -> Completion:
        if not self.is_configured():
            raise LLMUnavailable("No GEMINI_API_KEY in .env.")

        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": f"{system}\n\n{user}"}]}
            ],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": max_output_tokens,
                "responseMimeType": "application/json",
            },
        }

        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{GEMINI_BASE}/models/{self._settings.GEMINI_MODEL}:generateContent",
                headers={
                    "x-goog-api-key": self._settings.GEMINI_API_KEY.get_secret_value(),
                    "content-type": "application/json",
                },
                json=payload,
            )
        response.raise_for_status()
        data = response.json()

        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError(
                "The AI provider returned no answer. Try asking again, or write "
                "the SQL by hand."
            )
        candidate = candidates[0]

        if candidate.get("finishReason") in _BLOCKED_REASONS:
            raise LLMError(
                "The AI provider declined to answer this question. Rephrase it "
                "and try again."
            )

        # Parts can be more than one. Join them; a part with no "text" key
        # contributes nothing rather than breaking the join.
        text = "".join(
            part.get("text", "")
            for part in candidate.get("content", {}).get("parts", [])
        )
        usage = data.get("usageMetadata", {})

        return Completion(
            text=text,
            model=data.get("modelVersion") or self._settings.GEMINI_MODEL,
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            finish_reason=candidate.get("finishReason"),
        )
