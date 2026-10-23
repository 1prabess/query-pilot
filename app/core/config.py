from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, model_validator
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
