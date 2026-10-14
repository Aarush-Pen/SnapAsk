"""Gemini via the official `google-genai` SDK (Gemini Developer API)."""

import re

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .. import config
from ..effort_capabilities import Effort
from .base import BaseProvider, ErrorKind, ModelInfo, ProviderError, Result, Usage

# Levels per model live in effort_capabilities.GEMINI; 2.5 models take a token
# budget instead of a level name, mapped here.
BUDGETS = {"off": 0, "low": 1024, "medium": 8192, "high": 24576}
# A thinking model missing from the table still gets the two levels every
# Gemini 3+ thinking model accepts (unchanged from before the table existed).
UNLISTED_THINKING = Effort(("low", "high"), None, "level")

_NOT_CHAT = re.compile(
    r"tts|image|live|audio|embedding|transcribe|translate|omni|robotics|"
    r"nano-banana|computer-use|veo|lyria|-exp\b|-preview-\d|-\d{3}$")


def _info(model_id, name="", thinking=None):
    return ModelInfo(model_id, name, meta={"thinking": bool(thinking)})


class GeminiProvider(BaseProvider):
    id = "gemini"
    name = "Gemini"
    vendor = "Google"
    console_url = "https://aistudio.google.com/usage"
    keys_url = "https://aistudio.google.com/apikey"
    key_placeholder = "AIza…"
    default_model = "gemini-4-argon"
    fallback_models = (
        _info("gemini-4-argon", "Gemini 4 Argon"),
        _info("gemini-3.8-flash", "Gemini 3.8 Flash"),
        _info("gemini-3.6-flash", "Gemini 3.6 Flash"),
        _info("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"),
        _info("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview"),
        _info("gemini-3-flash-preview", "Gemini 3 Flash Preview"),
        _info("gemini-2.5-pro", "Gemini 2.5 Pro"),
        _info("gemini-2.5-flash", "Gemini 2.5 Flash"),
        _info("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite"),
    )

    def _client(self, key, timeout=config.REQUEST_TIMEOUT):
        return genai.Client(api_key=key, vertexai=False,
                            http_options=types.HttpOptions(timeout=int(timeout * 1000)))

    # --- models ---------------------------------------------------------------
    def list_models(self, key):
        client = self._client(key, timeout=20)
        try:
            models = list(client.models.list(config={"page_size": 200}))
        except Exception as e:
            raise _map_error(e) from e
        finally:
            client.close()
        out = []
        for m in models:
            mid = (m.name or "").removeprefix("models/")
            if (not mid.startswith("gemini-") or _NOT_CHAT.search(mid)
                    or "generateContent" not in (m.supported_actions or [])):
                continue
            out.append(_info(mid, m.display_name or "", m.thinking))
        rank = {m.id: n for n, m in enumerate(self.fallback_models)}
        out.sort(key=lambda m: (rank.get(m.id, len(rank)), [-ord(c) for c in m.id]))
        return out

    def effort_spec(self, model_id, known=()):
        spec = super().effort_spec(model_id, known)
        if spec is None and self.model_info(model_id, known).meta.get("thinking"):
            return UNLISTED_THINKING
        return spec

    # --- request --------------------------------------------------------------
    def request_settings(self, info, effort, known=()):
        params = {}
        spec = self.effort_spec(info.id, known)
        if spec is not None and effort in spec.levels:
            if spec.param == "budget":
                params["thinking_config"] = {"thinking_budget": BUDGETS[effort]}
            else:
                params["thinking_config"] = {"thinking_level": effort.upper()}
        return params

    def send(self, image, prompt, model, effort, *, key, system, job=None, known=()):
        info = self.model_info(model, known)
        params = self.request_settings(info, effort, known)
        self.log_request(model, effort, params)
        cfg = types.GenerateContentConfig(system_instruction=system, **params)
        client = self._client(key)
        if job is not None:
            job.on_cancel(client.close)       # aborts the in-flight HTTP request
        parts, meta, version, blocked = [], None, model, None
        try:
            for chunk in client.models.generate_content_stream(
                    model=model, config=cfg,
                    contents=[types.Part.from_bytes(data=image, mime_type="image/png"), prompt]):
                if job is not None and job.cancelled.is_set():
                    return None
                meta = chunk.usage_metadata or meta
                version = chunk.model_version or version
                feedback = getattr(chunk, "prompt_feedback", None)
                if feedback is not None and feedback.block_reason:
                    blocked = str(feedback.block_reason)
                for cand in chunk.candidates or []:
                    if cand.finish_reason and str(cand.finish_reason).split(".")[-1] in (
                            "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"):
                        blocked = str(cand.finish_reason)
                    for part in (cand.content.parts if cand.content else None) or []:
                        if part.text and not part.thought:
                            parts.append(part.text)
        except Exception as e:
            if job is not None and job.cancelled.is_set():
                return None
            err = _map_error(e)
            if err.message == _EFFORT_REJECTED:
                err = self.effort_error(model, effort if params else None)
            raise err from e
        finally:
            client.close()

        usage = _usage(meta, version)
        text = "".join(parts).strip()
        if blocked and not text:
            raise ProviderError(ErrorKind.REFUSED, "Gemini declined to answer this", usage)
        return Result(text or "?", usage, version)


_EFFORT_REJECTED = "This model doesn't take that thinking level"


def _usage(meta, model):
    if meta is None:
        return Usage(model=model)
    prompt = (meta.prompt_token_count or 0) + (meta.tool_use_prompt_token_count or 0)
    cached = meta.cached_content_token_count or 0       # part of prompt_token_count
    thoughts = meta.thoughts_token_count or 0           # not part of candidates_token_count
    return Usage(
        model=model,
        input_tokens=prompt - cached,
        cached_tokens=cached,
        output_tokens=(meta.candidates_token_count or 0) + thoughts,
        reasoning_tokens=thoughts,
    )


def _map_error(e):
    if isinstance(e, ProviderError):
        return e
    if isinstance(e, genai_errors.APIError):
        code, status = e.code or 0, (e.status or "").upper()
        msg = e.message or str(e)
        low = msg.lower()
        if "api key" in low or "api_key" in low or code == 401:
            return ProviderError(ErrorKind.AUTH, "Invalid Gemini API key")
        if code == 403 or status == "PERMISSION_DENIED":
            return ProviderError(ErrorKind.AUTH, "This key can't use the Gemini API")
        if code == 404:
            return ProviderError(ErrorKind.MODEL, "Model not available for this key")
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            if any(w in low for w in ("credit", "billing", "prepay", "spend")):
                return ProviderError(ErrorKind.NO_CREDIT, "Out of Gemini credits")
            return ProviderError(ErrorKind.RATE_LIMIT, "Gemini quota or rate limit hit")
        if code == 400 and "image" in low:
            return ProviderError(ErrorKind.NO_IMAGE, "This model can't read images")
        if code == 400 and "thinking" in low:
            return ProviderError(ErrorKind.BAD_REQUEST, _EFFORT_REJECTED)
        if code >= 500:
            return ProviderError(ErrorKind.SERVER, f"Gemini server error ({code})")
        return ProviderError(ErrorKind.BAD_REQUEST, BaseProvider.short(msg))
    if isinstance(e, httpx.TimeoutException):
        return ProviderError(ErrorKind.NETWORK, "Gemini timed out")
    if isinstance(e, httpx.TransportError):
        return ProviderError(ErrorKind.NETWORK, "Can't reach Google, check your connection")
    return ProviderError(ErrorKind.OTHER, BaseProvider.short(f"{type(e).__name__}: {e}"))
