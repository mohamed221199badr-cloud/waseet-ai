"""Application configuration, loaded from environment / .env."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://postgres@localhost:5433/waseet_dev"
    app_name: str = "Waseet AI"


settings = Settings()
