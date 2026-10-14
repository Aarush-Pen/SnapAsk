"""The provider interface. The rest of the app only ever talks to BaseProvider."""

import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum

from .. import effort_capabilities, pricing

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str = ""
    meta: dict = field(default_factory=dict, hash=False, compare=False)  # provider-specific

    @property
    def label(self):
        return self.name or self.id

    def to_json(self):
        return asdict(self)

    @classmethod
    def from_json(cls, d):
        return cls(id=d["id"], name=d.get("name", ""), meta=d.get("meta") or {})


@dataclass
class Usage:
    """Token counts, normalized so pricing.cost() works the same for every provider."""
    model: str = ""
    input_tokens: int = 0          # billed at the base input rate (excludes cache hits/writes)
    cached_tokens: int = 0         # cache hits
    cache_write_tokens: int = 0    # cache writes (all TTLs)
    cache_write_1h_tokens: int = 0 #   of which 1-hour writes
    output_tokens: int = 0         # everything billed as output, reasoning included
    reasoning_tokens: int = 0      #   of which reasoning/thinking (informational)
    attempts: list = field(default_factory=list)   # per-model parts when >1 model ran

    def summary(self):
        return (f"in={self.input_tokens} cached={self.cached_tokens} "
                f"cache_write={self.cache_write_tokens} out={self.output_tokens} "
                f"reasoning={self.reasoning_tokens}")


@dataclass
class Result:
    text: str
    usage: Usage
    model: str


class ErrorKind(str, Enum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    NO_CREDIT = "no_credit"
    NO_IMAGE = "no_image"
    MODEL = "model"
    NETWORK = "network"
    SERVER = "server"
    REFUSED = "refused"
    BAD_REQUEST = "bad_request"
    OTHER = "other"


class ProviderError(Exception):
    """A failure worth showing the user, as one short line."""

    def __init__(self, kind, message, usage=None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.usage = usage           # tokens that were billed anyway, if known


class Job:
    """One in-flight request. cancel() closes the HTTP stream, which is what
    tells the API to stop generating."""

    def __init__(self):
        self.cancelled = threading.Event()
        self._closers = []
        self._lock = threading.Lock()

    def on_cancel(self, fn):
        with self._lock:
            if not self.cancelled.is_set():
                self._closers.append(fn)
                return
        fn()

    def cancel(self):
        with self._lock:
            self.cancelled.set()
            closers, self._closers = self._closers, []
        for fn in closers:
            try:
                fn()
            except Exception:
                pass


class BaseProvider(ABC):
    id = ""                 # "claude"
    name = ""               # shown in the UI: "Claude"
    vendor = ""             # "Anthropic"
    console_url = ""        # where the user checks their balance
    keys_url = ""
    key_placeholder = ""    # "sk-ant-…"
    default_model = ""
    fallback_models = ()    # ModelInfo list used before a live list is available
    has_balance_api = False

    # --- the interface ------------------------------------------------------
    def validate_key(self, key):
        """Cheap check (lists models). Returns the image-capable models or
        raises ProviderError."""
        return self.list_models(key)

    @abstractmethod
    def list_models(self, key):
        """Image-capable models for this key, best first."""

    @abstractmethod
    def send(self, image, prompt, model, effort, *, key, system, job=None, known=()):
        """Send a PNG screenshot plus prompt. Returns Result, None if cancelled,
        or raises ProviderError. `known` is the cached model list (capabilities)."""

    def cost_from_usage(self, usage):
        """USD for a Usage, or None if the model isn't in pricing.py."""
        return pricing.cost(self.id, usage)

    def fetch_balance(self, key):
        """Remaining prepaid balance in USD, if the provider exposes one."""
        return None

    # --- helpers --------------------------------------------------------------
    def model_info(self, model_id, known=()):
        """ModelInfo for an id: from the live list if present, else a bare one."""
        for m in list(known) + list(self.fallback_models):
            if m.id == model_id:
                return m
        return ModelInfo(model_id)

    def effort_spec(self, model_id, known=()):
        """effort_capabilities.Effort for this model, or None if unknown
        (unknown models get no effort control and no effort parameter)."""
        return effort_capabilities.lookup(self.id, model_id)

    def effort_to_send(self, model_id, effort, known=()):
        """`effort` if this model accepts it, else None (and say why in the log)."""
        spec = self.effort_spec(model_id, known)
        if effort is None or spec is None or effort not in spec.levels:
            if effort is not None:
                log.warning("not sending effort %r: %s doesn't list it", effort, model_id)
            return None
        return effort

    def effort_error(self, model_id, effort):
        """Message for a 400 that rejected the effort setting."""
        name = effort_capabilities.LABELS.get(effort, effort) if effort else "default"
        return ProviderError(ErrorKind.BAD_REQUEST,
                             f"{model_id} rejected effort “{name}”. Pick another level")

    @staticmethod
    def mask_key(key):
        if not key:
            return ""
        key = key.strip()
        head = key[:7] if key.startswith("sk-ant-") else key[:4] if key.startswith("AIza") else key[:3]
        return f"{head}…{key[-4:]}"

    @staticmethod
    def short(text, limit=90):
        text = " ".join(str(text).split())
        return text if len(text) <= limit else text[:limit - 1] + "…"

    def log_request(self, model, effort, settings):
        log.info("request provider=%s model=%s effort=%s settings=%s",
                 self.id, model, effort, settings)
