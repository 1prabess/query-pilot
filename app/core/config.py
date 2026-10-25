from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    ENVIRONMENT: Literal["local", "staging", "production"] = "local"
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str

    SQL_ECHO: bool = False

    DB_POOL_SIZE: int = Field(default=10, ge=1, le=100)
    DB_MAX_OVERFLOW: int = Field(default=20, ge=0, le=200)

    SECRET_KEY: str
    ACCESS_TOKEN_TTL_MINUTES: int = Field(default=15, ge=1, le=1440)
    REFRESH_TOKEN_TTL_DAYS: int = Field(default=30, ge=1, le=365)
    MAGIC_LINK_TTL_MINUTES: int = Field(default=15, ge=1, le=1440)
    JWT_ALGORITHM: Literal["HS256", "HS384", "HS512"] = "HS256"
    CREDENTIAL_ENCRYPTION_KEY: SecretStr

    # Which provider answers a question. `auto` prefers Gemini and falls back
    # to Ollama. Both sets of settings can be present at once -- this is what
    # picks between them, so switching does not mean unsetting a key.
    AI_PROVIDER: Literal["gemini", "ollama", "auto"] = "auto"

    GEMINI_API_KEY: SecretStr = SecretStr("")
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"

    OLLAMA_BASE_URL: str | None = None
    OLLAMA_MODEL: str | None = None

    # The context window Ollama gives the model. It is memory, not just a
    # limit: the KV cache grows with the window, and on a small laptop it is
    # the difference between a usable machine and a frozen one.
    OLLAMA_NUM_CTX: int = Field(default=4096, ge=512, le=131_072)

    # How long Ollama keeps the model in memory after the last request.
    # "0" unloads it straight away, which is what a small machine wants; the
    # cost is that every request reloads the model from disk.
    OLLAMA_KEEP_ALIVE: str = "0"

    AI_SCHEMA_TOKEN_BUDGET: int = Field(default=12_000, ge=1_000, le=200_000)

    AI_MAX_OUTPUT_TOKENS: int = Field(default=4096, ge=256, le=32_000)

    # How long a query may run against the customer's database before we
    # give up on it. This is the ceiling for every plan: the plan sets what
    # its own tier is allowed, and this holds all of them down. Lower it to
    # squeeze every tier, raise it to let the paid tiers use their longer
    # timeouts.
    QUERY_EXECUTION_TIMEOUT_MS: int = Field(default=60_000, ge=100, le=600_000)

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @model_validator(mode="after")
    def _reject_placeholder_secret_in_production(self) -> Self:
        if self.is_production and self.SECRET_KEY.startswith("replace-me"):
            raise ValueError(
                "SECRET_KEY must be a real secret when ENVIRONMENT=production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore
