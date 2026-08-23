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
    mcp_api_key: str | None = None

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
