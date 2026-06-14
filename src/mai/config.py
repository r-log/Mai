from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./mai.db"
    ledger_path: str = "./mai-data"
    github_token: str | None = None
    github_api_url: str = "https://api.github.com"


settings = Settings()
