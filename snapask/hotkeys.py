"""Global hotkeys through a Quartz event tap on the main run loop.

Needs Accessibility permission. The same tap also records new shortcuts, so a
combo the app already owns (e.g. ⌃A) can be captured without triggering it.
"""

import logging
from dataclasses import dataclass

import Quartz
from PyObjCTools import AppHelper

from .config import SWALLOW_HOTKEYS

log = logging.getLogger(__name__)

MOD_ORDER = ("ctrl", "alt", "shift", "cmd")          # Apple's display order
MOD_SYMBOLS = {"ctrl": "⌃", "alt": "⌥", "shift": "⇧", "cmd": "⌘"}
MOD_FLAGS = {
    "ctrl": Quartz.kCGEventFlagMaskControl,
    "alt": Quartz.kCGEventFlagMaskAlternate,
    "shift": Quartz.kCGEventFlagMaskShift,
    "cmd": Quartz.kCGEventFlagMaskCommand,
}

# macOS virtual keycodes -> names (ANSI layout).
KEY_NAMES = {
    0: "A", 1: "S", 2: "D", 3: "F", 4: "H", 5: "G", 6: "Z", 7: "X", 8: "C", 9: "V",
    11: "B", 12: "Q", 13: "W", 14: "E", 15: "R", 16: "Y", 17: "T", 18: "1", 19: "2",
    20: "3", 21: "4", 22: "6", 23: "5", 24: "=", 25: "9", 26: "7", 27: "-", 28: "8",
    29: "0", 30: "]", 31: "O", 32: "U", 33: "[", 34: "I", 35: "P", 36: "↩", 37: "L",
    38: "J", 39: "'", 40: "K", 41: ";", 42: "\\", 43: ",", 44: "/", 45: "N", 46: "M",
    47: ".", 48: "⇥", 49: "Space", 50: "`", 51: "⌫", 53: "⎋", 117: "⌦",
    122: "F1", 120: "F2", 99: "F3", 118: "F4", 96: "F5", 97: "F6", 98: "F7",
    100: "F8", 101: "F9", 109: "F10", 103: "F11", 111: "F12", 105: "F13",
    107: "F14", 113: "F15", 123: "←", 124: "→", 125: "↓", 126: "↑",
    115: "↖", 119: "↘", 116: "⇞", 121: "⇟",
}
KEY_ESCAPE = 53


@dataclass(frozen=True)
class Combo:
    key: int
    mods: frozenset

    @classmethod
    def of(cls, key, *mods):
        return cls(key, frozenset(mods))

    @property
    def label(self):
        return ("".join(MOD_SYMBOLS[m] for m in MOD_ORDER if m in self.mods)
                + KEY_NAMES.get(self.key, f"Key {self.key}"))

    @property
    def is_valid(self):
        """Needs ⌃ or ⌥, or ⌘ plus another modifier: bare keys and plain ⌘-combos
        belong to the front app."""
        if self.key == KEY_ESCAPE and not self.mods:
            return False
        return bool(self.mods & {"ctrl", "alt"}) or ("cmd" in self.mods and len(self.mods) > 1)

    def to_json(self):
        return {"key": self.key, "mods": [m for m in MOD_ORDER if m in self.mods]}

    @classmethod
    def from_json(cls, d):
        return cls(int(d["key"]), frozenset(m for m in d.get("mods", ()) if m in MOD_FLAGS))


@dataclass(frozen=True)
class Action:
    id: str
    title: str
    default: Combo


ACTIONS = (
    Action("capture", "Take screenshot", Combo.of(0, "ctrl")),           # ⌃A, as before
    Action("cancel", "Cancel / hide answer", Combo.of(1, "ctrl")),       # ⌃S, as before
    Action("toggle_window", "Show / hide settings", Combo.of(43, "ctrl", "alt")),  # ⌃⌥,
    Action("cycle_provider", "Switch provider", Combo.of(35, "ctrl", "alt")),      # ⌃⌥P
)
ACTION_IDS = tuple(a.id for a in ACTIONS)


class HotkeyManager:
    def __init__(self, on_action, wants_escape=None):
        self.on_action = on_action        # called on the main thread with an action id
        # While wants_escape() is true (a selection is on screen), ⎋ is taken
        # from the front app and reported as the "escape" action.
        self.wants_escape = wants_escape
        self.bindings = {a.id: a.default for a in ACTIONS}
        self._tap = None
        self._source = None
        self._recorder = self._on_invalid = None

    @property
    def running(self):
        return self._tap is not None

    def start(self):
        if self._tap is not None:
            return True
        self._callback = self._on_event              # keep a reference alive
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault, 1 << Quartz.kCGEventKeyDown,
            self._callback, None)
        if not tap:
            return False
        self._tap = tap
        self._source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetMain(), self._source,
                                  Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        log.info("hotkeys listening")
        return True

    def stop(self):
        self.stop_recording()
        if self._tap is None:
            return
        Quartz.CGEventTapEnable(self._tap, False)
        Quartz.CFRunLoopRemoveSource(Quartz.CFRunLoopGetMain(), self._source,
                                     Quartz.kCFRunLoopCommonModes)
        Quartz.CFMachPortInvalidate(self._tap)
        self._tap = self._source = None
        log.info("hotkeys stopped")

    # --- recording a new shortcut ---------------------------------------------
    def record(self, on_done, on_invalid=None):
        """The next valid combo goes to on_done(combo) instead of an action;
        on_done(None) means cancelled (⎋). Combos that can't be hotkeys go to
        on_invalid(combo) and recording continues."""
        self._recorder = on_done
        self._on_invalid = on_invalid

    def stop_recording(self):
        self._recorder = self._on_invalid = None

    @property
    def recording(self):
        return self._recorder is not None

    # --- the tap -------------------------------------------------------------
    def _on_event(self, proxy, etype, event, refcon):
        try:
            return self._handle(etype, event)
        except Exception:
            log.exception("hotkey handler failed")
            return event                           # never eat keys because of a bug

    def _handle(self, etype, event):
        if etype in (Quartz.kCGEventTapDisabledByTimeout,
                     Quartz.kCGEventTapDisabledByUserInput):
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)
            return event
        if etype != Quartz.kCGEventKeyDown:
            return event

        flags = Quartz.CGEventGetFlags(event)
        code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        combo = Combo(code, frozenset(m for m, f in MOD_FLAGS.items() if flags & f))
        repeat = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat)

        if self._recorder is not None:
            if repeat:
                pass
            elif combo.is_valid or (code == KEY_ESCAPE and not combo.mods):
                callback = self._recorder
                self.stop_recording()
                AppHelper.callAfter(callback, combo if combo.is_valid else None)
            elif self._on_invalid is not None:
                AppHelper.callAfter(self._on_invalid, combo)
            return None                            # keys typed while recording go nowhere

        if code == KEY_ESCAPE and not combo.mods and self.wants_escape and self.wants_escape():
            if not repeat:
                AppHelper.callAfter(self.on_action, "escape")
            return None

        action = next((a for a, c in self.bindings.items() if c == combo), None)
        if action is None:
            return event
        if not repeat:
            # Run after the tap returns: a slow handler would get the tap disabled.
            AppHelper.callAfter(self.on_action, action)
        return None if SWALLOW_HOTKEYS else event
