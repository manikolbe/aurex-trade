"""Authentication configuration."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthConfig(BaseSettings):
    """Configuration for Google OAuth authentication, loaded from environment / .env."""

    model_config = SettingsConfigDict(env_prefix="AUTH_", env_file=".env", extra="ignore")

    google_client_id: str = ""
    google_client_secret: str = ""
    redirect_uri: str = "http://localhost:8000/auth/callback"
    allowed_emails: list[str] = ["manikolbe@gmail.com"]
    session_expiry_hours: int = 48
    cookie_secure: bool = False
    secret_key: str = ""

    @field_validator("allowed_emails", mode="before")
    @classmethod
    def parse_comma_separated(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [email.strip() for email in v.split(",") if email.strip()]
        if isinstance(v, list):
            return list(v)
        return []
