"""Claude via the official `anthropic` SDK (Messages API)."""

import base64

import anthropic

from .. import config
from .base import BaseProvider, ErrorKind, ModelInfo, ProviderError, Result, Usage

# Models that accept server-side refusal fallbacks ("default" routing). On a
# safety decline the API re-runs the request on Anthropic's recommended model
# inside the same call instead of returning nothing.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = ("claude-fable-5-1", "claude-fable-5", "claude-opus-5-5",
                   "claude-opus-5", "claude-sonnet-5-5")

MAX_TOKENS = 32000             # cap on thinking + answer, as before


def _info(model_id, name, adaptive=True, max_tokens=128000, api_effort=None):
    meta = {"adaptive": adaptive, "max_tokens": max_tokens}
    if api_effort is not None:
        meta["api_effort"] = api_effort    # what /v1/models reports; only for checking the table
    return ModelInfo(model_id, name, meta)


class ClaudeProvider(BaseProvider):
    id = "claude"
    name = "Claude"
    vendor = "Anthropic"
    console_url = "https://platform.claude.com/settings/billing"
    keys_url = "https://platform.claude.com/settings/keys"
    key_placeholder = "sk-ant-…"
    default_model = "claude-opus-5-5"
    # Used until a live list arrives; mirrors /v1/models capabilities (2026-10-14).
    fallback_models = (
        _info("claude-opus-5-5", "Claude Opus 5.5"),
        _info("claude-sonnet-5-5", "Claude Sonnet 5.5"),
        _info("claude-haiku-5-5", "Claude Haiku 5.5"),
        _info("claude-fable-5-1", "Claude Fable 5.1"),
        _info("claude-opus-5", "Claude Opus 5"),
        _info("claude-sonnet-5", "Claude Sonnet 5"),
        _info("claude-opus-4-8", "Claude Opus 4.8"),
        _info("claude-haiku-4-5-20251001", "Claude Haiku 4.5", adaptive=False, max_tokens=64000),
    )

    def _client(self, key, timeout=config.REQUEST_TIMEOUT):
        return anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=1)

    # --- models ---------------------------------------------------------------
    def list_models(self, key):
        try:
            models = list(self._client(key, timeout=20).models.list(limit=100))
        except Exception as e:
            raise _map_error(e) from e
        out = []
        for m in models:
            if m.deprecated_at or (m.lifecycle and m.lifecycle != "active"):
                continue
            caps = m.capabilities.model_dump() if m.capabilities else {}
            if not _supported(caps, "image_input"):
                continue
            api_effort = [lv for lv in ("low", "medium", "high", "xhigh", "max")
                          if _supported(caps, "effort", lv)]
            out.append(_info(m.id, m.display_name,
                             _supported(caps, "thinking", "types", "adaptive"),
                             m.max_tokens or MAX_TOKENS, api_effort))
        return out

    # --- request --------------------------------------------------------------
    def request_settings(self, info, effort):
        """The non-secret parameters sent for this model + effort level."""
        params = {"max_tokens": min(MAX_TOKENS, info.meta.get("max_tokens") or MAX_TOKENS)}
        effort = self.effort_to_send(info.id, effort)
        if effort:
            if info.meta.get("adaptive", True):
                # Opus 4.6-4.8 only think when asked; on newer models this is the default.
                params["thinking"] = {"type": "adaptive"}
            params["output_config"] = {"effort": effort}
        if info.id in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def send(self, image, prompt, model, effort, *, key, system, job=None, known=()):
        info = self.model_info(model, known)
        params = self.request_settings(info, effort)
        self.log_request(model, effort, params)
        content = [
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.b64encode(image).decode("ascii")}},
            {"type": "text", "text": prompt},
        ]
        try:
            with self._client(key).beta.messages.stream(
                model=model, system=system,
                messages=[{"role": "user", "content": content}], **params,
            ) as stream:
                if job is not None:
                    job.on_cancel(stream.close)
                for _ in stream.text_stream:
                    if job is not None and job.cancelled.is_set():
                        return None
                message = stream.get_final_message()
        except Exception as e:
            if job is not None and job.cancelled.is_set():
                return None
            err = _map_error(e)
            if err.kind == ErrorKind.BAD_REQUEST and _about_effort(e):
                err = self.effort_error(model, params.get("output_config", {}).get("effort"))
            raise err from e

        usage = _usage(message)
        if message.stop_reason == "refusal":
            raise ProviderError(ErrorKind.REFUSED, "Claude declined to answer this", usage)
        # After a fallback, only text from the model that finished counts.
        blocks = list(message.content)
        last_switch = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
        text = "".join(b.text for b in blocks[last_switch + 1:] if b.type == "text").strip()
        return Result(text or "?", usage, message.model)


def _about_effort(e):
    text = str(getattr(e, "message", "") or e).lower()
    return "effort" in text or "thinking" in text


def _supported(caps, *path):
    node = caps
    for p in path:
        node = node.get(p) if isinstance(node, dict) else None
    return bool(isinstance(node, dict) and node.get("supported"))


def _part(u, model):
    created = getattr(u, "cache_creation", None)
    return Usage(
        model=model,
        input_tokens=u.input_tokens or 0,
        cached_tokens=u.cache_read_input_tokens or 0,
        cache_write_tokens=u.cache_creation_input_tokens or 0,
        cache_write_1h_tokens=getattr(created, "ephemeral_1h_input_tokens", 0) or 0,
        output_tokens=u.output_tokens or 0,          # includes thinking
    )


def _usage(message):
    u = message.usage
    usage = _part(u, message.model)
    details = getattr(u, "output_tokens_details", None)
    usage.reasoning_tokens = getattr(details, "thinking_tokens", 0) or 0
    # With a fallback, top-level usage covers only the final attempt; the
    # per-attempt bill is in usage.iterations.
    iterations = [it for it in (getattr(u, "iterations", None) or []) if getattr(it, "model", None)]
    if len(iterations) > 1:
        usage.attempts = [_part(it, it.model) for it in iterations]
    return usage


def _map_error(e):
    if isinstance(e, ProviderError):
        return e
    if isinstance(e, anthropic.AuthenticationError):
        return ProviderError(ErrorKind.AUTH, "Invalid Anthropic API key")
    if isinstance(e, anthropic.PermissionDeniedError):
        return ProviderError(ErrorKind.AUTH, "This key can't use that model")
    if isinstance(e, anthropic.NotFoundError):
        return ProviderError(ErrorKind.MODEL, "Model not available for this key")
    if isinstance(e, anthropic.RateLimitError):
        return ProviderError(ErrorKind.RATE_LIMIT, "Claude rate limit hit, try again shortly")
    if isinstance(e, anthropic.APIStatusError):
        msg = (getattr(e, "message", "") or str(e)).lower()
        if e.status_code == 402 or "credit balance" in msg or "billing" in msg:
            return ProviderError(ErrorKind.NO_CREDIT, "Out of Anthropic credits")
        if e.status_code == 400 and "image" in msg:
            return ProviderError(ErrorKind.NO_IMAGE, "This model can't read images")
        if e.status_code == 529 or "overloaded" in msg:
            return ProviderError(ErrorKind.SERVER, "Claude is overloaded, try again")
        if e.status_code >= 500:
            return ProviderError(ErrorKind.SERVER, f"Anthropic server error ({e.status_code})")
        return ProviderError(ErrorKind.BAD_REQUEST, BaseProvider.short(getattr(e, "message", str(e))))
    if isinstance(e, anthropic.APITimeoutError):
        return ProviderError(ErrorKind.NETWORK, "Claude timed out")
    if isinstance(e, anthropic.APIConnectionError):
        return ProviderError(ErrorKind.NETWORK, "Can't reach Anthropic, check your connection")
    return ProviderError(ErrorKind.OTHER, BaseProvider.short(f"{type(e).__name__}: {e}"))
