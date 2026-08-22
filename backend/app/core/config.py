from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str

    app_host: str = "127.0.0.1"
    app_port: int = 8000

    db_pool_min: int = 2
    db_pool_max: int = 10
    db_lock_timeout_ms: int = 2000
    db_statement_timeout_ms: int = 5000

    sample_interval_sec: int = 2
    prediction_horizon_sec: int = 30

    experiment_mode_enabled: bool = False

    admin_api_token: str
    internal_api_token: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()