from langchain_openai import ChatOpenAI

from app.config import get_settings


def get_chat_model() -> ChatOpenAI:
    """Build a ChatOpenAI client pointed at OpenRouter.

    Called lazily inside graph nodes (never at import time) so importing the
    graph module works even before OPENROUTER_API_KEY is filled in.
    """
    settings = get_settings()
    if not settings.openrouter_api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Add it to .env before running the graph."
        )
    return ChatOpenAI(
        model=settings.openrouter_model,
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key,
        temperature=0.2,
        timeout=settings.request_timeout_seconds,
        default_headers={
            "HTTP-Referer": "http://localhost",
            "X-Title": "operation-homebound-stage2",
        },
    )
