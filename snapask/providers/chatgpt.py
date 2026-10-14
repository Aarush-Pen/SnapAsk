"""ChatGPT via the official `openai` SDK (Responses API)."""

import base64
import re

import openai

from .. import config
from .base import BaseProvider, ErrorKind, ModelInfo, ProviderError, Result, Usage

_VISION_FAMILIES = re.compile(r"^(gpt-6|gpt-5|gpt-4\.1|gpt-4o|o1|o3|o4)")
_NOT_CHAT = re.compile(
    r"audio|realtime|transcribe|tts|search|image|codex|instruct|chat-latest|"
    r"deep-research|embedding|moderation|cyber|live|translate|whisper|"
    r"^o1-mini|^o1-preview|^o3-mini|-pro\b")
_DATED = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def _info(model_id, name=""):
    return ModelInfo(model_id, name)


class OpenAIProvider(BaseProvider):
    id = "openai"
    name = "ChatGPT"
    vendor = "OpenAI"
    console_url = "https://platform.openai.com/settings/organization/billing/overview"
    keys_url = "https://platform.openai.com/api-keys"
    key_placeholder = "sk-…"
    default_model = "gpt-6.1-sol"
    fallback_models = tuple(_info(m) for m in (
        "gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna", "gpt-6-sol",
        "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.4",
        "gpt-5.4-mini", "gpt-4.1", "gpt-4o",
    ))

    def _client(self, key, timeout=config.REQUEST_TIMEOUT):
        return openai.OpenAI(api_key=key, timeout=timeout, max_retries=1)

    # --- models ---------------------------------------------------------------
    def list_models(self, key):
        try:
            ids = [m.id for m in self._client(key, timeout=20).models.list()]
        except Exception as e:
            raise _map_error(e) from e
        ids = [i for i in ids if _VISION_FAMILIES.match(i) and not _NOT_CHAT.search(i)
               and not _DATED.search(i)]
        rank = {m.id: n for n, m in enumerate(self.fallback_models)}
        ids.sort(key=lambda i: (rank.get(i, len(rank)), [-ord(c) for c in i]))
        return [_info(i) for i in ids]

    # --- request --------------------------------------------------------------
    def request_settings(self, info, effort):
        params = {"store": False}           # don't keep screenshots on OpenAI's side
        # The app never sends temperature/top_p: reasoning models reject them.
        effort = self.effort_to_send(info.id, effort)
        if effort:
            params["reasoning"] = {"effort": effort}
        return params

    def send(self, image, prompt, model, effort, *, key, system, job=None, known=()):
        info = self.model_info(model, known)
        params = self.request_settings(info, effort)
        self.log_request(model, effort, params)
        data_url = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
        parts, response = [], None
        try:
            stream = self._client(key).responses.create(
                model=model, instructions=system, stream=True,
                input=[{"role": "user", "content": [
                    {"type": "input_image", "image_url": data_url},
                    {"type": "input_text", "text": prompt},
                ]}],
                **params,
            )
            if job is not None:
                job.on_cancel(stream.close)
            with stream:
                for event in stream:
                    if job is not None and job.cancelled.is_set():
                        return None
                    if event.type == "response.output_text.delta":
                        parts.append(event.delta)
                    elif event.type in ("response.completed", "response.incomplete"):
                        response = event.response
                    elif event.type == "response.failed":
                        response = event.response
                        err = response.error
                        raise _status_error(getattr(err, "code", ""),
                                            getattr(err, "message", "request failed"),
                                            _usage(response))
                    elif event.type == "error":
                        raise _status_error(getattr(event, "code", ""),
                                            getattr(event, "message", "request failed"))
        except Exception as e:
            if job is not None and job.cancelled.is_set():
                return None
            err = _map_error(e)
            if isinstance(e, openai.BadRequestError) and _about_effort(e):
                err = self.effort_error(model, params.get("reasoning", {}).get("effort"))
            raise err from e

        usage = _usage(response) if response is not None else Usage(model=model)
        text = "".join(parts).strip()
        if not text and response is not None and response.status == "incomplete":
            reason = getattr(response.incomplete_details, "reason", "")
            if reason == "content_filter":
                raise ProviderError(ErrorKind.REFUSED, "ChatGPT declined to answer this", usage)
        return Result(text or "?", usage, getattr(response, "model", model))


def _usage(response):
    u = getattr(response, "usage", None)
    if u is None:
        return Usage(model=getattr(response, "model", ""))
    cached = getattr(u.input_tokens_details, "cached_tokens", 0) or 0
    return Usage(
        model=response.model,
        input_tokens=(u.input_tokens or 0) - cached,       # input_tokens includes cache hits
        cached_tokens=cached,
        output_tokens=u.output_tokens or 0,                 # includes reasoning tokens
        reasoning_tokens=getattr(u.output_tokens_details, "reasoning_tokens", 0) or 0,
    )


def _about_effort(e):
    text = str(getattr(e, "message", "") or e).lower()
    return "reasoning" in text or "effort" in text


def _status_error(code, message, usage=None):
    code, low = (code or "").lower(), (message or "").lower()
    if code == "insufficient_quota" or "quota" in low or "billing" in low:
        kind, text = ErrorKind.NO_CREDIT, "Out of OpenAI credits"
    elif "rate" in code:
        kind, text = ErrorKind.RATE_LIMIT, "ChatGPT rate limit hit, try again shortly"
    elif "image" in low and ("support" in low or "invalid" in low):
        kind, text = ErrorKind.NO_IMAGE, "This model can't read images"
    else:
        kind, text = ErrorKind.SERVER, BaseProvider.short(message)
    return ProviderError(kind, text, usage)


def _map_error(e):
    if isinstance(e, ProviderError):
        return e
    if isinstance(e, openai.AuthenticationError):
        return ProviderError(ErrorKind.AUTH, "Invalid OpenAI API key")
    if isinstance(e, openai.PermissionDeniedError):
        return ProviderError(ErrorKind.AUTH, "This key can't use that model")
    if isinstance(e, openai.NotFoundError):
        return ProviderError(ErrorKind.MODEL, "Model not available for this key")
    if isinstance(e, openai.RateLimitError):
        if getattr(e, "code", None) == "insufficient_quota":
            return ProviderError(ErrorKind.NO_CREDIT, "Out of OpenAI credits")
        return ProviderError(ErrorKind.RATE_LIMIT, "ChatGPT rate limit hit, try again shortly")
    if isinstance(e, openai.BadRequestError):
        msg = (getattr(e, "message", "") or str(e)).lower()
        if "image" in msg and ("support" in msg or "invalid" in msg):
            return ProviderError(ErrorKind.NO_IMAGE, "This model can't read images")
        return ProviderError(ErrorKind.BAD_REQUEST, BaseProvider.short(getattr(e, "message", str(e))))
    if isinstance(e, openai.APIStatusError):
        if e.status_code >= 500:
            return ProviderError(ErrorKind.SERVER, f"OpenAI server error ({e.status_code})")
        return ProviderError(ErrorKind.BAD_REQUEST, BaseProvider.short(getattr(e, "message", str(e))))
    if isinstance(e, openai.APITimeoutError):
        return ProviderError(ErrorKind.NETWORK, "ChatGPT timed out")
    if isinstance(e, openai.APIConnectionError):
        return ProviderError(ErrorKind.NETWORK, "Can't reach OpenAI, check your connection")
    return ProviderError(ErrorKind.OTHER, BaseProvider.short(f"{type(e).__name__}: {e}"))
