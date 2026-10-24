import httpx

from app.llm.base import Completion


class Ollama:
    name = "ollama"

    def __init__(self, settings) -> None:
        self._settings = settings

    def is_configured(self) -> bool:
        return bool(self._settings.OLLAMA_BASE_URL and self._settings.OLLAMA_MODEL)

    async def complete(
        self, system: str, user: str, *, max_output_tokens: int
    ) -> Completion:
        payload = {
            "model": self._settings.OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "num_predict": max_output_tokens},
        }
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{self._settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat",
                json=payload,
            )
        response.raise_for_status()
        data = response.json()

        return Completion(
            text=data["message"]["content"],
            model=data.get("model") or self._settings.OLLAMA_MODEL,
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            finish_reason=data.get("done_reason"),
        )
