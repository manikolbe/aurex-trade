"""Authentication configuration."""

import secrets

import structlog
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


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
            return [email.strip().lower() for email in v.split(",") if email.strip()]
        if isinstance(v, list):
            return [str(e).lower() for e in v]
        return []

    @model_validator(mode="after")
    def validate_auth_config(self) -> "AuthConfig":
        """Warn on missing credentials, auto-generate secret_key if empty."""
        if not self.google_client_id or not self.google_client_secret:
            logger.warning(
                "auth.config_incomplete",
                hint="Set AUTH_GOOGLE_CLIENT_ID and AUTH_GOOGLE_CLIENT_SECRET to enable login",
            )
        if not self.secret_key:
            # Auto-generate for development; logs warning so it's not silent
            self.secret_key = secrets.token_urlsafe(32)
            logger.warning(
                "auth.secret_key_generated",
                hint="Set AUTH_SECRET_KEY in .env for stable CSRF tokens across restarts",
            )
        if not self.allowed_emails:
            logger.warning(
                "auth.no_allowed_emails",
                hint="AUTH_ALLOWED_EMAILS is empty — all logins will be denied",
            )
        return self
