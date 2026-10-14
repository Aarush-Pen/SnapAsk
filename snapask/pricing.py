"""Per-token prices used to estimate what each request cost.

None of the three providers exposes a prepaid-balance endpoint for a standard API
key, so SnapAsk subtracts these estimates from the starting balance you enter.

HOW TO UPDATE: edit the tables below. Prices are USD per 1 million tokens.
Model ids match by longest prefix, so "claude-haiku-4-5" also covers the dated id
"claude-haiku-4-5-20251001". A model missing from its table is used normally,
but its requests aren't subtracted from the balance (the window says so).

Sources (checked 2026-10-14):
  Claude  https://platform.claude.com/docs/en/about-claude/pricing
  OpenAI  https://developers.openai.com/api/docs/pricing      (Standard tier)
  Gemini  https://ai.google.dev/gemini-api/docs/pricing       (Paid tier, Standard)
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Price:
    input: float                     # uncached input tokens
    output: float                    # output tokens, including reasoning/thinking
    cache_read: float | None = None  # cached input; None = billed as input
    cache_write: float | None = None # Claude 5-minute cache writes; None = as input
    cache_write_1h: float | None = None
    long_context: tuple | None = None   # (prompt-token threshold, Price above it)
    changes_on: tuple | None = None     # (date, Price from that day on)

    def on(self, day):
        if self.changes_on and day >= self.changes_on[0]:
            return self.changes_on[1].on(day)
        return self

    def for_prompt(self, prompt_tokens):
        if self.long_context and prompt_tokens > self.long_context[0]:
            return self.long_context[1]
        return self


def _c(inp, w5, w1h, hit, out, **kw):
    return Price(input=inp, output=out, cache_read=hit, cache_write=w5,
                 cache_write_1h=w1h, **kw)


def _o(inp, cached, out, **kw):
    return Price(input=inp, output=out, cache_read=cached, **kw)


def _g(inp, cached, out, **kw):
    return Price(input=inp, output=out, cache_read=cached, **kw)


def _later(day, price):
    return (date.fromisoformat(day), price)


# --- Claude (Anthropic) ------------------------------------------------------
# columns: base input, 5m cache write, 1h cache write, cache hit, output
CLAUDE = {
    "claude-fable-5-1":   _c(10, 12.50, 20, 0.25, 50),
    "claude-mythos-5-1":  _c(10, 12.50, 20, 0.25, 50),
    "claude-fable-5":     _c(10, 12.50, 20, 1.00, 50),
    "claude-mythos-5":    _c(10, 12.50, 20, 1.00, 50),
    "claude-opus-5-5":    _c(4, 5.00, 8, 0.20, 20),
    "claude-opus-5":      _c(5, 6.25, 10, 0.50, 25),
    "claude-opus-4-8":    _c(5, 6.25, 10, 0.50, 25),
    "claude-opus-4-7":    _c(5, 6.25, 10, 0.50, 25),
    "claude-opus-4-6":    _c(5, 6.25, 10, 0.50, 25),
    "claude-opus-4-5":    _c(5, 6.25, 10, 0.50, 25),
    "claude-opus-4-1":    _c(15, 18.75, 30, 1.50, 75),
    "claude-sonnet-5-5":  _c(2, 2.50, 4, 0.10, 10),
    "claude-sonnet-5":    _c(2, 2.50, 4, 0.20, 10),
    "claude-sonnet-4-6":  _c(3, 3.75, 6, 0.30, 15),
    "claude-sonnet-4-5":  _c(3, 3.75, 6, 0.30, 15),
    "claude-haiku-5-5":   _c(0.10, 0.125, 0.20, 0.01, 0.50,
                             long_context=(100_000, _c(0.50, 0.625, 1, 0.05, 2.50))),
    "claude-haiku-4-5":   _c(1, 1.25, 2, 0.10, 5),
}

# --- OpenAI ------------------------------------------------------------------
# columns: input, cached input, output.  Long context = prompts over 272K tokens.
_LONG = 272_000
OPENAI = {
    "gpt-6-astra":        _o(10, 1.00, 50, long_context=(_LONG, _o(20, 2.00, 75))),
    "gpt-6.1-sol":        _o(2, 0.10, 10, long_context=(_LONG, _o(4, 0.20, 15))),
    "gpt-6-sol":          _o(2, 0.20, 10, long_context=(_LONG, _o(4, 0.40, 15))),
    "gpt-6-luna":         _o(0.10, 0.01, 0.50, long_context=(_LONG, _o(0.20, 0.02, 0.75))),
    # GPT-5.6 Sol is promotional pricing "at least through November 21, 2026"
    "gpt-5.6-sol":        _o(4, 0.40, 20, long_context=(_LONG, _o(8, 0.80, 30))),
    "gpt-5.6-terra":      _o(2, 0.20, 12, long_context=(_LONG, _o(4, 0.40, 18))),
    "gpt-5.6-luna":       _o(0.20, 0.02, 1.20, long_context=(_LONG, _o(0.40, 0.04, 1.80))),
    "gpt-5.5":            _o(5, 0.50, 30, long_context=(_LONG, _o(10, 1.00, 45))),
    "gpt-5.5-pro":        _o(30, None, 180, long_context=(_LONG, _o(60, None, 270))),
    "gpt-5.4":            _o(2.50, 0.25, 15, long_context=(_LONG, _o(5, 0.50, 22.50))),
    "gpt-5.4-pro":        _o(30, None, 180, long_context=(_LONG, _o(60, None, 270))),
    "gpt-5.4-mini":       _o(0.75, 0.075, 4.50),
    "gpt-5.4-nano":       _o(0.20, 0.02, 1.25),
    "gpt-5.2":            _o(1.75, 0.175, 14),
    "gpt-5.2-pro":        _o(21, None, 168),
    "gpt-5.1":            _o(1.25, 0.125, 10),
    "gpt-5":              _o(1.25, 0.125, 10),
    "gpt-5-mini":         _o(0.25, 0.025, 2),
    "gpt-5-nano":         _o(0.05, 0.005, 0.40),
    "gpt-5-pro":          _o(15, None, 120),
    "gpt-4.1":            _o(2, 0.50, 8),
    "gpt-4.1-mini":       _o(0.40, 0.10, 1.60),
    "gpt-4.1-nano":       _o(0.10, 0.025, 0.40),
    "gpt-4o":             _o(2.50, 1.25, 10),
    "gpt-4o-2024-05-13":  _o(5, None, 15),
    "gpt-4o-mini":        _o(0.15, 0.075, 0.60),
    "o1":                 _o(15, 7.50, 60),
    "o1-pro":             _o(150, None, 600),
    "o3":                 _o(2, 0.50, 8),
    "o3-mini":            _o(1.10, 0.55, 4.40),
    "o3-pro":             _o(20, None, 80),
    "o4-mini":            _o(1.10, 0.275, 4.40),
}

# --- Gemini (Google) ---------------------------------------------------------
# columns: input (text/image), cached input, output (thinking included).
# Several prices double on a published date; changes_on switches automatically.
GEMINI = {
    "gemini-4-argon":         _g(2.00, 0.10, 10, changes_on=_later("2026-12-01", _g(4.00, 0.20, 20))),
    "gemini-3.8-flash":       _g(0.75, 0.075, 3.75, changes_on=_later("2027-01-01", _g(1.50, 0.15, 7.50))),
    "gemini-3.6-flash":       _g(0.75, 0.075, 3.75, changes_on=_later("2027-01-01", _g(1.50, 0.15, 7.50))),
    "gemini-3.5-flash-lite":  _g(0.30, 0.03, 2.50),
    "gemini-3.1-flash-lite":  _g(0.25, 0.025, 1.50),
    "gemini-3.1-pro-preview": _g(2.00, 0.20, 12, long_context=(200_000, _g(4.00, 0.40, 18))),
    "gemini-3-flash-preview": _g(0.50, 0.05, 3.00),
    "gemini-2.5-pro":         _g(1.25, 0.125, 10, long_context=(200_000, _g(2.50, 0.25, 15))),
    "gemini-2.5-flash":       _g(0.30, 0.03, 2.50),
    "gemini-2.5-flash-lite":  _g(0.10, 0.01, 0.40),
}

TABLES = {"claude": CLAUDE, "openai": OPENAI, "gemini": GEMINI}


def price_for(provider_id, model, day=None):
    """The Price for a model, or None if it isn't in the table."""
    table = TABLES[provider_id]
    model = model.removeprefix("models/")
    best = max((k for k in table if model == k or model.startswith(k + "-")),
               key=len, default=None)
    if best is None:
        return None
    return table[best].on(day or date.today())


def cost(provider_id, usage, day=None):
    """USD cost of a normalized providers.base.Usage, or None if unpriced.

    Each attempt is priced at its own model's rates (a Claude request that was
    retried on a fallback model bills both attempts).
    """
    total = 0.0
    for part in usage.attempts or [usage]:
        price = price_for(provider_id, part.model, day)
        if price is None:
            return None
        prompt = part.input_tokens + part.cached_tokens + part.cache_write_tokens
        p = price.for_prompt(prompt)
        cache_read = p.cache_read if p.cache_read is not None else p.input
        cache_write = p.cache_write if p.cache_write is not None else p.input
        cache_write_1h = p.cache_write_1h if p.cache_write_1h is not None else cache_write
        total += (part.input_tokens * p.input
                  + part.cached_tokens * cache_read
                  + (part.cache_write_tokens - part.cache_write_1h_tokens) * cache_write
                  + part.cache_write_1h_tokens * cache_write_1h
                  + part.output_tokens * p.output) / 1_000_000
    return total
