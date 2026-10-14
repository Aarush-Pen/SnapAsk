"""Which effort levels each model accepts, using each provider's own level names.

This is the one place to update when models change. Each entry lists the
levels in order (lowest first) exactly as the API spells them, plus the API's
default level (None if the docs don't state one).

A model that isn't listed has no effort control in the app and no effort
parameter is sent: guessing risks a 400. Claude and OpenAI ids match exactly,
or as a dated snapshot of a listed id ("claude-haiku-4-5-20251001",
"gpt-5.4-2026-03-05"), never by looser prefixes ("gpt-5.4-mini" is not
"gpt-5.4"). Gemini keeps matching by model family, as before.

LAST VERIFIED: 2026-10-14
  Claude  https://platform.claude.com/docs/en/build-with-claude/effort
          (cross-checked with GET /v1/models `capabilities.effort`)
  OpenAI  https://developers.openai.com/api/docs/models/<model id>
          https://developers.openai.com/api/docs/guides/reasoning
  Gemini  https://ai.google.dev/gemini-api/docs/thinking
"""

import re
from dataclasses import dataclass

# Every level name any provider uses, lowest to highest. Gemini 2.5's "off"
# (thinking budget 0) sits where "none" does. Used to pick the closest level
# when a model doesn't accept the saved one.
ORDER = ("none", "off", "minimal", "low", "medium", "high", "xhigh", "max")
LABELS = {"none": "None", "off": "Off", "minimal": "Minimal", "low": "Low",
          "medium": "Medium", "high": "High", "xhigh": "Extra high", "max": "Max"}


@dataclass(frozen=True)
class Effort:
    levels: tuple            # accepted values, lowest first; () = model has no effort control
    default: str | None      # the API's default level, if documented
    param: str = ""          # Gemini only: "level" (thinking_level) or "budget" (thinking_budget)


NOT_SUPPORTED = Effort((), None)

_ALL5 = ("low", "medium", "high", "xhigh", "max")
_NO_XHIGH = ("low", "medium", "high", "max")

# --- Claude: output_config.effort ------------------------------------------
CLAUDE = {
    "claude-fable-5-1":  Effort(_ALL5, "high"),
    "claude-fable-5":    Effort(_ALL5, "high"),
    "claude-opus-5-5":   Effort(_ALL5, "medium"),
    "claude-opus-5":     Effort(_ALL5, "high"),
    "claude-opus-4-8":   Effort(_ALL5, "high"),
    "claude-opus-4-7":   Effort(_ALL5, "high"),
    "claude-opus-4-6":   Effort(_NO_XHIGH, "high"),
    "claude-opus-4-5":   Effort(("low", "medium", "high"), "high"),
    "claude-sonnet-5-5": Effort(_ALL5, "high"),
    "claude-sonnet-5":   Effort(_ALL5, "high"),
    "claude-sonnet-4-6": Effort(_NO_XHIGH, "high"),
    "claude-sonnet-4-5": NOT_SUPPORTED,
    "claude-haiku-5-5":  Effort(_ALL5, "medium"),
    "claude-haiku-4-5":  NOT_SUPPORTED,
}

# --- OpenAI: reasoning.effort (Responses API) --------------------------------
_N5 = ("none", "low", "medium", "high", "xhigh")
_N6 = ("none", "low", "medium", "high", "xhigh", "max")
OPENAI = {
    "gpt-6-astra":   Effort(_ALL5, None),
    "gpt-6.1-sol":   Effort(_ALL5, "medium"),
    "gpt-6-sol":     Effort(_N6, "medium"),
    "gpt-6-luna":    Effort(_N6, "medium"),
    "gpt-5.6-sol":   Effort(_N6, "medium"),
    "gpt-5.6-terra": Effort(_N6, "medium"),
    "gpt-5.6-luna":  Effort(_N6, "medium"),
    "gpt-5.5":       Effort(_N5, "medium"),
    "gpt-5.4":       Effort(_N5, "none"),
    "gpt-5.4-mini":  Effort(_N5, "none"),
    "gpt-5.4-nano":  Effort(_N5, "none"),
    "gpt-5.2":       Effort(_N5, "none"),
    "gpt-5.1":       Effort(("none", "low", "medium", "high"), "none"),
    "gpt-5":         Effort(("minimal", "low", "medium", "high"), None),
    # Non-reasoning models: no reasoning parameter at all.
    "gpt-4.1":       NOT_SUPPORTED,
    "gpt-4.1-mini":  NOT_SUPPORTED,
    "gpt-4.1-nano":  NOT_SUPPORTED,
    "gpt-4o":        NOT_SUPPORTED,
    "gpt-4o-mini":   NOT_SUPPORTED,
    # Left out on purpose (docs don't list their levels): gpt-5-mini,
    # gpt-5-nano, o4-mini, o3, o1. They work, but with no effort control.
}

# --- Gemini: thinking_level (3+) or thinking_budget (2.5) ---------------------
_LMH = ("low", "medium", "high")
GEMINI = {
    "gemini-4-argon":        Effort(_LMH, "medium", "level"),
    "gemini-3.8-flash":      Effort(_LMH, "medium", "level"),
    "gemini-3.6-flash":      Effort(("minimal",) + _LMH, "medium", "level"),
    "gemini-3.5-flash-lite": Effort(("minimal",) + _LMH, "minimal", "level"),
    "gemini-3.1-pro":        Effort(_LMH, "high", "level"),
    "gemini-3-flash":        Effort(("minimal",) + _LMH, "high", "level"),
    "gemini-3-pro":          Effort(("low", "high"), "high", "level"),
    "gemini-2.5-pro":        Effort(_LMH, None, "budget"),        # can't switch thinking off
    "gemini-2.5-flash":      Effort(("off",) + _LMH, None, "budget"),
    "gemini-2.5-flash-lite": Effort(("off",) + _LMH, "off", "budget"),
}

TABLES = {"claude": CLAUDE, "openai": OPENAI, "gemini": GEMINI}

_SNAPSHOT = re.compile(r"-(\d{8}|\d{4}-\d{2}-\d{2})$")   # -20251001 or -2026-03-05


def lookup(provider_id, model_id):
    """The Effort for a model, or None if the model isn't in the table."""
    table = TABLES[provider_id]
    model_id = model_id.removeprefix("models/")
    if provider_id == "gemini":
        family = max((k for k in table if model_id == k or model_id.startswith(k + "-")),
                     key=len, default=None)
        return table.get(family)
    if model_id in table:
        return table[model_id]
    return table.get(_SNAPSHOT.sub("", model_id))


def snap(level, levels):
    """`level` if the model accepts it, else the closest accepted level,
    preferring the next lower one. None if the model has no levels."""
    if not levels:
        return None
    if level in levels:
        return level
    rank = ORDER.index(level) if level in ORDER else ORDER.index("high")
    lower = [lv for lv in levels if ORDER.index(lv) < rank]
    return lower[-1] if lower else levels[0]


def label(level, default=None):
    text = LABELS.get(level, level)
    return f"{text} (default)" if level == default else text
