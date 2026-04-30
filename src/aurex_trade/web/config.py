"""Web server configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class WebConfig(BaseSettings):
    """Configuration for the web server, loaded from environment / .env."""

    model_config = SettingsConfigDict(env_prefix="WEB_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = False
    log_level: str = "INFO"
