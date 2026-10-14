"""Behavior and look of the screenshot flow (carried over from the original snapask.py)."""

DEFAULT_EFFORT = "high"        # starting effort level, snapped to what the model accepts

DOT_DIAMETER = 7               # points; the black dot where a selection starts

ANSWER_FONT_SIZE = 11
ANSWER_MARGIN = 4              # gap from the screen's bottom-left corner
ANSWER_MAX_WIDTH = 520         # longer answers get truncated with "…"
ANSWER_SECONDS = 0             # 0 = stay until next capture / cancel
TOAST_SECONDS = 2.5            # short notices ("Provider: Gemini", "Add your key…")

MIN_SELECTION = 5              # points; a smaller drag (or a click) cancels
MAX_IMAGE_EDGE = 1568          # bigger screenshots get shrunk before sending
SWALLOW_HOTKEYS = True         # stop the hotkeys reaching the front app

REQUEST_TIMEOUT = 300.0        # seconds

SYSTEM_PROMPT = (
    "You are looking at a region of someone's screen. Reply with ONLY the "
    "answer: a letter, number, word, or short phrase. No explanation, no "
    "reasoning, no full sentences, no quotation marks, no markdown. For "
    "multiple choice, give just the option (e.g. B). For math, give just the "
    "result and include proper units. If the image isn't a question, reply with "
    "the single most useful short thing (e.g. a translation, or the name of "
    "what's shown). If it's unreadable, reply ?"
)
USER_PROMPT = "Answer."
