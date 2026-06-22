from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://ragbot:ragbot@localhost:5432/ragbot"
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_checkpoint_pool_size: int = 5

    # Chat LLM — routed through OpenRouter
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openai_model: str = "openrouter/free"
    openai_fallback_model: str = "deepseek/deepseek-v4-flash"

    # Embeddings — same key/base as chat; override only if using a different provider
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "openai/text-embedding-3-small"
    embedding_dim: int = 1536

    # RAG tuning
    chunk_size: int = 768
    chunk_overlap: int = 128
    top_k_results: int = 10
    history_max_tokens: int = 8000
    retrieval_max_tokens: int = 3000
    hnsw_ef_search: int = 160
    hnsw_iterative_scan: str = "relaxed_order"

    # Channels
    telegram_bot_token: str = ""
    wa_phone_number_id: str = ""
    wa_verify_token: str = ""

    # Security
    secret_key: str = "changeme"
    fernet_key: str = ""
    csrf_secret: str = ""
    operator_token: str = ""  # if set, used for operator/admin auth instead of secret_key

    # LangSmith
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "langraph-bot-v1"
    langsmith_hide_inputs: bool = False
    langsmith_hide_outputs: bool = False

    # Observability
    sentry_dsn: str = ""
    environment: str = "dev"

    # STT
    groq_api_key: str = ""

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_domain: str = "localhost:8000"

    # Optional
    openai_vision_model: str = ""
    web_search_url: str = ""

    @property
    def effective_embedding_base_url(self) -> str:
        return self.embedding_base_url or self.openrouter_base_url

    @property
    def effective_embedding_api_key(self) -> str:
        return self.embedding_api_key or self.openrouter_api_key


settings = Settings()

PLAN_LIMITS: dict[str, dict] = {
    "free":  {"docs": 5,   "chunks": 500,   "queries_monthly": 500},
    "basic": {"docs": 20,  "chunks": 2000,  "queries_monthly": 2000},
    "pro":   {"docs": 100, "chunks": 10000, "queries_monthly": 10000},
}
