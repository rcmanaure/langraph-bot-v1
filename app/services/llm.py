from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.config import settings


def get_chat_llm(fallback: bool = False) -> ChatOpenAI:
    model = (settings.openai_fallback_model if fallback else None) or settings.openai_model
    return ChatOpenAI(
        model=model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        default_headers={"HTTP-Referer": f"https://{settings.app_domain}"},
    )


def get_embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.effective_embedding_api_key,
        base_url=settings.effective_embedding_base_url,
        dimensions=settings.embedding_dim,
    )
