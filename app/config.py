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

    @model_validator(mode="after")
    def _secret_must_be_real_outside_dev(self):
        if self.app_env != "dev" and (
            self.jwt_secret.startswith("dev-only") or len(self.jwt_secret) < 32
        ):
            raise ValueError("JWT_SECRET must be set to a random value of 32+ characters")
        return self


settings = Settings()
