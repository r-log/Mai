from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./mai.db"
    ledger_path: str = "./mai-data"
    github_token: str | None = None
    github_api_url: str = "https://api.github.com"
    firecrawl_api_key: str | None = None
    firecrawl_api_url: str = "https://api.firecrawl.dev"
    ips_bug_tracker_url: str = "https://www.getmangos.eu/bug-tracker/"
    openrouter_api_key: str | None = None
    openrouter_api_url: str = "https://openrouter.ai/api"
    enrichment_model: str = "moonshotai/kimi-k2.5"


settings = Settings()
