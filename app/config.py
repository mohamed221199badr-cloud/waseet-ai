"""Application configuration, loaded from environment / .env."""

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://postgres@localhost:5433/waseet_dev"
    app_name: str = "Waseet AI"
    app_env: str = "dev"  # anything other than "dev" requires a real JWT_SECRET
    jwt_secret: str = "dev-only-secret-change-me-0123456789"
    jwt_expire_minutes: int = 480
    # Milestone 4: set TELEGRAM_BOT_TOKEN in your own environment/.env. Never hardcode or commit it.
    telegram_bot_token: str = ""
    telegram_link_code_ttl_hours: int = 24
    # Milestone 5: conversational order agent (rules + DB decide; the LLM is optional).
    chat_agent_enabled: bool = True       # CHAT_AGENT_ENABLED=false -> old one-shot intake
    chat_show_prices: bool = True         # prices in the confirmation message (from DB only)
    chat_session_ttl_minutes: int = 30
    llm_mode: str = "off"                 # off | assist
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:1.5b"
    llm_timeout_seconds: float = 8.0

    @model_validator(mode="after")
    def _normalize_database_url(self):
        # Neon/Supabase/Heroku-style URLs come as postgres:// or postgresql://;
        # make the psycopg2 driver explicit so both forms work unchanged.
        if self.database_url.startswith("postgres://"):
            self.database_url = self.database_url.replace(
                "postgres://", "postgresql+psycopg2://", 1)
        elif self.database_url.startswith("postgresql://"):
            self.database_url = self.database_url.replace(
                "postgresql://", "postgresql+psycopg2://", 1)
        return self

    @model_validator(mode="after")
    def _secret_must_be_real_outside_dev(self):
        if self.app_env != "dev" and (
            self.jwt_secret.startswith("dev-only") or len(self.jwt_secret) < 32
        ):
            raise ValueError("JWT_SECRET must be set to a random value of 32+ characters")
        return self


settings = Settings()
