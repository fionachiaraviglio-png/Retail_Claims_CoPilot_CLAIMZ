from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = Field(default="", alias="ANTHROPIC_BASE_URL")
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Agent model configuration
    primary_model: str = Field(default="claude-opus-4-7", alias="ANTHROPIC_MODEL")
    fast_model: str = Field(default="claude-haiku-4-5-20251001", alias="CLAUDE_FAST_MODEL")
    notifications_enabled: bool = Field(default=False, alias="NOTIFICATIONS_ENABLED")

    model_config = {"env_file": ".env", "populate_by_name": True}


settings = Settings()
