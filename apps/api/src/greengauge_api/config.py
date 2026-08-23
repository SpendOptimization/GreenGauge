from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "GreenGauge API"
    database_path: str = "./data/greengauge.sqlite3"
    cors_origins: str = "http://localhost:3000"
    github_repository: str = Field(
        default="rohanmalige/GreenGauge",
        validation_alias=AliasChoices("GITHUB_REPOSITORY", "GREENGAUGE_GITHUB_REPOSITORY"),
    )
    github_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GITHUB_TOKEN", "GREENGAUGE_GITHUB_TOKEN"),
    )
    github_webhook_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GITHUB_WEBHOOK_SECRET", "GREENGAUGE_GITHUB_WEBHOOK_SECRET"),
    )
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "GREENGAUGE_OPENAI_API_KEY"),
    )
    recommendation_model: str = Field(
        default="gpt-5-nano-2025-08-07",
        validation_alias=AliasChoices("RECOMMENDATION_MODEL", "GREENGAUGE_RECOMMENDATION_MODEL"),
    )
    embedding_model: str = "text-embedding-3-small"
    recommendation_min_similarity: float = 0.60
    recommendation_min_success_rate: float = 0.60
    mcp_api_key: str | None = None
    model_pricing_json: str = (
        '{"terra":{"input":0.25,"cached_input":0.025,"cache_write":0.25,"output":1.5},'
        '"sonnet":{"input":3.0,"cached_input":0.3,"cache_write":3.0,"output":15.0},'
        '"sol":{"input":2.5,"cached_input":0.25,"cache_write":2.5,"output":15.0}}'
    )

    model_config = SettingsConfigDict(
        env_prefix="GREENGAUGE_",
        env_file=(".env", "../../.env"),
        extra="ignore",
    )

    @property
    def database_file(self) -> Path:
        return Path(self.database_path).expanduser().resolve()

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
