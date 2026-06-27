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
    enrichment_concurrency: int = 8
    embedding_api_key: str | None = None
    embedding_api_url: str = "https://openrouter.ai/api"
    embedding_model: str = "openai/text-embedding-3-small"
    embedding_dimensions: int = 1536
    drift_subsystem_depth: int = 3
    git_mirror_dir: str = "./mirrors"
    git_worktree_dir: str = "./worktrees"
    refresh_interval_seconds: int = 10800
    deploy_command: str | None = None
    session_secret: str = "dev-insecure-change-me"
    cookie_secure: bool = True
    web_host: str = "127.0.0.1"
    web_port: int = 8000
    forwarded_allow_ips: str = "127.0.0.1"
    review_model: str = "anthropic/claude-sonnet-4.6"
    review_model_large: str = "google/gemini-2.5-pro"
    review_hunk_routing_threshold: int = 8
    review_large_context_chars: int = 24000


settings = Settings()
