"""Provider registry, in the order the switcher shows them."""

from .base import (BaseProvider, ErrorKind, Job,
                   ModelInfo, ProviderError, Result, Usage)
from .chatgpt import OpenAIProvider
from .claude import ClaudeProvider
from .gemini import GeminiProvider

PROVIDERS = {p.id: p for p in (ClaudeProvider(), OpenAIProvider(), GeminiProvider())}
ORDER = list(PROVIDERS)

__all__ = ["PROVIDERS", "ORDER", "BaseProvider", "ModelInfo", "Usage", "Result", "Job",
           "ProviderError", "ErrorKind"]
