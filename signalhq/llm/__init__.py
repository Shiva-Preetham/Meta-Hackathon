"""Model access. ``build_gateway`` picks the transport for the current mode."""

from __future__ import annotations

from signalhq.config import Settings
from signalhq.db import Database
from signalhq.llm.gateway import LLMGateway


def build_gateway(db: Database, settings: Settings) -> LLMGateway:
    """Online: Meta Model API via the OpenAI SDK. Offline: the heuristic fake transport."""
    if settings.is_offline:
        from signalhq.llm.offline import OfflineHeuristicTransport

        return LLMGateway(db, settings, OfflineHeuristicTransport())
    from signalhq.llm.transport import OpenAICompatTransport
    from signalhq.llm.voice import HttpVoiceTransport

    assert settings.model_api_key  # is_offline is False only when a key is set
    return LLMGateway(
        db,
        settings,
        OpenAICompatTransport(base_url=settings.api_base_url, api_key=settings.model_api_key),
        HttpVoiceTransport(settings.voice_url, settings.model_api_key),
    )


__all__ = ["LLMGateway", "build_gateway"]
