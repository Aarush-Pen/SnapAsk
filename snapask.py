#!/usr/bin/env python3
"""
snapask.py - drag-to-ask screenshot helper for macOS.

  Ctrl+A   toggle screenshot mode (a tiny black dot follows under the cursor)
  drag     select a region -> it's sent to Claude -> the answer appears,
           small, in the bottom-left corner of that screen
  Ctrl+S   cancel the pending request (nothing is shown) / hide the answer
  click    clicking the answer copies the full text to the clipboard

Setup (see README.md):
  pip install -r requirements.txt
  cp .env.example .env        # then paste your key into .env
  python snapask.py

macOS permissions (System Settings > Privacy & Security), granted to the app
you launch this from (Terminal, iTerm, VS Code, ...):
  - Accessibility     -> to catch Ctrl+A / Ctrl+S globally
  - Screen Recording  -> to capture the region you select
Restart that app after granting them.

Quit with Ctrl+C in the terminal.
"""

import base64
import os
import signal
import subprocess
import sys
import tempfile
import threading
import traceback
from pathlib import Path

import anthropic
from dotenv import load_dotenv
import objc
import Quartz
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSEvent,
    NSFont,
    NSLineBreakByTruncatingTail,
    NSMakeRect,
    NSPanel,
    NSPasteboard,
    NSPasteboardTypeString,
    NSRectFillUsingOperation,
    NSScreen,
    NSScreenSaverWindowLevel,
    NSTextField,
    NSView,
)
from Foundation import NSRunLoop, NSRunLoopCommonModes, NSTimer
from PyObjCTools import AppHelper

# ----------------------------------------------------------------- settings --
MODEL = "claude-opus-5-5"      # or "claude-sonnet-5" / "claude-haiku-4-5-20251001"
EFFORT = "high"                # how hard it thinks: "low", "medium", "high",
                               # "xhigh", "max" (higher = smarter, slower, pricier)
MAX_TOKENS = 32000             # cap on thinking + answer; thinking counts toward it

DOT_SIZE = 5                   # points
DOT_OFFSET = (3, -24)          # from the cursor tip; negative y = below it

ANSWER_FONT_SIZE = 11
ANSWER_MARGIN = 4              # gap from the screen's bottom-left corner
ANSWER_MAX_WIDTH = 520         # longer answers get truncated with "…"
ANSWER_SECONDS = 0             # 0 = stay until next Ctrl+A / Ctrl+S

MIN_SELECTION = 4              # drags smaller than this are ignored (a click)
MAX_IMAGE_EDGE = 1568          # Claude's recommended max; bigger gets shrunk
SWALLOW_HOTKEYS = True         # stop Ctrl+A / Ctrl+S reaching the front app

KEY_A, KEY_S = 0, 1            # macOS virtual keycodes

SYSTEM_PROMPT = (
    "You are looking at a region of someone's screen. Reply with ONLY the "
    "answer: a letter, number, word, or short phrase. No explanation, no "
    "reasoning, no full sentences, no quotation marks, no markdown. For "
    "multiple choice, give just the option (e.g. B). For math, give just the "
    "result and include proper units. If the image isn't a question, reply with "
    "the single most useful short thing (e.g. a translation, or the name of "
    "what's shown). If it's unreadable, reply ?"
)

# ------------------------------------------------------------ window helpers --
_PANEL_STYLE = 0 | (1 << 7)                 # borderless + non-activating
_PANEL_BEHAVIOR = (1 << 0) | (1 << 4) | (1 << 8)  # all Spaces, stationary,
                                                  # over full-screen apps
_COMPOSITE_COPY = 1                         # NSCompositingOperationCopy

CONTROLLER = None


def make_panel(rect, level, clickable):
    p = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        rect, _PANEL_STYLE, NSBackingStoreBuffered, False
    )
    p.setOpaque_(False)
    p.setBackgroundColor_(NSColor.clearColor())
    p.setHasShadow_(False)
    p.setLevel_(level)
    p.setCollectionBehavior_(_PANEL_BEHAVIOR)
    p.setHidesOnDeactivate_(False)
    p.setReleasedWhenClosed_(False)
    p.setIgnoresMouseEvents_(not clickable)
    return p


class DotView(NSView):
    def drawRect_(self, rect):
        NSColor.blackColor().setFill()
        NSBezierPath.bezierPathWithOvalInRect_(self.bounds()).fill()


class SelectionView(NSView):
    """Full-screen, invisible view that catches the drag while armed."""

    def acceptsFirstMouse_(self, event):
        return True

    def mouseDown_(self, event):
        CONTROLLER.drag_began(NSEvent.mouseLocation())

    def mouseDragged_(self, event):
        CONTROLLER.drag_moved(NSEvent.mouseLocation())

    def mouseUp_(self, event):
        CONTROLLER.drag_ended(NSEvent.mouseLocation())

    def drawRect_(self, rect):
        NSColor.clearColor().set()
        NSRectFillUsingOperation(self.bounds(), _COMPOSITE_COPY)
        # 1%-alpha fill: invisible, but guarantees the window gets the clicks
        NSColor.colorWithWhite_alpha_(0.0, 0.01).setFill()
        NSBezierPath.fillRect_(self.bounds())

        sel = CONTROLLER.selection_rect()
        if sel is None:
            return
        o = self.window().frame().origin
        local = NSMakeRect(sel[0] - o.x, sel[1] - o.y, sel[2], sel[3])
        NSColor.colorWithWhite_alpha_(0.0, 0.06).setFill()
        NSBezierPath.fillRect_(local)
        NSColor.colorWithWhite_alpha_(0.0, 0.55).setStroke()
        path = NSBezierPath.bezierPathWithRect_(local)
        path.setLineWidth_(1.0)
        path.stroke()


class AnswerView(NSView):
    """The answer bubble. Any click on it copies the full answer."""

    def acceptsFirstMouse_(self, event):
        return True

    def hitTest_(self, point):
        hit = objc.super(AnswerView, self).hitTest_(point)
        return self if hit is not None else None     # label clicks come here too

    def mouseDown_(self, event):
        CONTROLLER.copy_answer()


# ---------------------------------------------------------------- capture ----
def capture_region(x, y, w, h):
    """Screenshot a rect given in points, top-left origin. Returns PNG bytes."""
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        subprocess.run(
            ["screencapture", "-x", "-t", "png", "-R", f"{x},{y},{w},{h}", path],
            check=True, capture_output=True,
        )
        _shrink(path)
        with open(path, "rb") as f:
            data = f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if not data:
        raise RuntimeError("empty screenshot (check Screen Recording permission)")
    return data


def _shrink(path):
    out = subprocess.run(
        ["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
        capture_output=True, text=True,
    ).stdout
    dims = [int(line.split()[-1]) for line in out.splitlines() if "pixel" in line]
    if dims and max(dims) > MAX_IMAGE_EDGE:
        subprocess.run(["sips", "-Z", str(MAX_IMAGE_EDGE), path], capture_output=True)


# ------------------------------------------------------------------- jobs ----
class Job:
    """One in-flight request. cancel() aborts the HTTP stream, which is what
    tells the API to stop generating."""

    def __init__(self):
        self.cancelled = threading.Event()
        self.stream = None
        self.lock = threading.Lock()

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            s = self.stream
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


# ------------------------------------------------------------- controller ----
class Controller:
    def __init__(self):
        self.client = anthropic.Anthropic(timeout=300.0, max_retries=1)   # key from .env
        self.armed = False
        self.overlays = []
        self.dot_timer = None
        self.drag_start = None
        self.drag_now = None
        self.job = None
        self._answer_token = None
        self._build_dot()
        self._build_answer()
        self._install_hotkeys()

    # ---- hotkeys (Quartz event tap on the main run loop)
    def _install_hotkeys(self):
        self._tap_cb = self._on_key_event          # keep a reference alive
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            1 << Quartz.kCGEventKeyDown,
            self._tap_cb,
            None,
        )
        if not self._tap:
            print("Couldn't listen for Ctrl+A / Ctrl+S.\n"
                  "Give your terminal app Accessibility access in System "
                  "Settings > Privacy & Security, restart it, and try again.")
            sys.exit(1)
        src = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src,
                                  Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)

    def _on_key_event(self, proxy, etype, event, refcon):
        if etype in (Quartz.kCGEventTapDisabledByTimeout,
                     Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(self._tap, True)
            return event
        if etype != Quartz.kCGEventKeyDown:
            return event

        flags = Quartz.CGEventGetFlags(event)
        others = (Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskAlternate
                  | Quartz.kCGEventFlagMaskShift)
        if not (flags & Quartz.kCGEventFlagMaskControl) or (flags & others):
            return event
        code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        if code not in (KEY_A, KEY_S):
            return event

        repeat = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat)
        if not repeat:
            try:
                self.toggle() if code == KEY_A else self.cancel()
            except Exception:
                traceback.print_exc()
        return None if SWALLOW_HOTKEYS else event

    # ---- Ctrl+A / Ctrl+S
    def toggle(self):
        if self.armed:
            self._disarm()
            return
        self._cancel_job()
        self._hide_answer()
        self._arm()

    def cancel(self):
        self._cancel_job()
        self._hide_answer()
        if self.armed:
            self._disarm()

    def _cancel_job(self):
        if self.job is not None:
            self.job.cancel()
            self.job = None

    # ---- screenshot mode
    def _arm(self):
        self.armed = True
        self.drag_start = self.drag_now = None
        self.overlays = []
        for screen in NSScreen.screens():
            f = screen.frame()
            p = make_panel(f, NSScreenSaverWindowLevel, clickable=True)
            p.setBackgroundColor_(NSColor.colorWithWhite_alpha_(0.0, 0.01))
            p.setContentView_(SelectionView.alloc().initWithFrame_(
                NSMakeRect(0, 0, f.size.width, f.size.height)))
            p.orderFrontRegardless()
            self.overlays.append(p)

        self._move_dot()
        self.dot.orderFrontRegardless()
        self.dot_timer = NSTimer.timerWithTimeInterval_repeats_block_(
            1 / 120, True, lambda t: self._move_dot())
        NSRunLoop.currentRunLoop().addTimer_forMode_(self.dot_timer, NSRunLoopCommonModes)

    def _disarm(self):
        self.armed = False
        if self.dot_timer is not None:
            self.dot_timer.invalidate()
            self.dot_timer = None
        self.dot.orderOut_(None)
        for p in self.overlays:
            p.orderOut_(None)
        self.overlays = []
        self.drag_start = self.drag_now = None

    def _build_dot(self):
        self.dot = make_panel(NSMakeRect(0, 0, DOT_SIZE, DOT_SIZE),
                              NSScreenSaverWindowLevel + 1, clickable=False)
        self.dot.setContentView_(
            DotView.alloc().initWithFrame_(NSMakeRect(0, 0, DOT_SIZE, DOT_SIZE)))

    def _move_dot(self):
        p = NSEvent.mouseLocation()
        self.dot.setFrameOrigin_((p.x + DOT_OFFSET[0] - DOT_SIZE / 2,
                                  p.y + DOT_OFFSET[1] - DOT_SIZE / 2))

    # ---- dragging (global Cocoa coords: origin bottom-left of main display)
    def selection_rect(self):
        if self.drag_start is None or self.drag_now is None:
            return None
        (x0, y0), (x1, y1) = self.drag_start, self.drag_now
        return (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))

    def _redraw(self):
        for p in self.overlays:
            p.contentView().setNeedsDisplay_(True)

    def drag_began(self, pt):
        self.drag_start = self.drag_now = (pt.x, pt.y)
        self._redraw()

    def drag_moved(self, pt):
        if self.drag_start is not None:
            self.drag_now = (pt.x, pt.y)
            self._redraw()

    def drag_ended(self, pt):
        if self.drag_start is None:
            return
        self.drag_now = (pt.x, pt.y)
        x, y, w, h = self.selection_rect()
        if w < MIN_SELECTION or h < MIN_SELECTION:     # just a click: stay armed
            self.drag_start = self.drag_now = None
            self._redraw()
            return

        screen = self._screen_at(*self.drag_start)
        primary_h = NSScreen.screens()[0].frame().size.height
        region = (round(x), round(primary_h - (y + h)), round(w), round(h))

        self._disarm()                                   # dot + overlay vanish
        job = Job()
        self.job = job
        # give the window server a moment to remove the overlay before capture
        AppHelper.callLater(0.08, self._start_job, job, region, screen)

    @staticmethod
    def _screen_at(x, y):
        for s in NSScreen.screens():
            f = s.frame()
            if (f.origin.x <= x < f.origin.x + f.size.width
                    and f.origin.y <= y < f.origin.y + f.size.height):
                return s
        return NSScreen.mainScreen()

    # ---- request
    def _start_job(self, job, region, screen):
        if job is not self.job or job.cancelled.is_set():
            return
        threading.Thread(target=self._worker, args=(job, region, screen),
                         daemon=True).start()

    def _worker(self, job, region, screen):
        try:
            png = capture_region(*region)
            if job.cancelled.is_set():
                return
            answer = self._ask_claude(job, png)
        except Exception:
            if job.cancelled.is_set():
                return
            traceback.print_exc()
            answer = "⚠"
        if answer is None or job.cancelled.is_set():
            return
        AppHelper.callAfter(self._deliver, job, answer, screen)

    def _ask_claude(self, job, png):
        parts = []
        with self.client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            extra_body={"output_config": {"effort": EFFORT}},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/png",
                        "data": base64.b64encode(png).decode("ascii")}},
                    {"type": "text", "text": "Answer."},
                ],
            }],
        ) as stream:
            with job.lock:
                job.stream = stream
            if job.cancelled.is_set():
                return None
            for text in stream.text_stream:
                if job.cancelled.is_set():
                    return None
                parts.append(text)
        answer = "".join(parts).strip()
        return answer or "?"

    def _deliver(self, job, answer, screen):
        if job is not self.job or job.cancelled.is_set():
            return                                      # cancelled: show nothing
        self.job = None
        self._show_answer(answer, screen)

    # ---- answer bubble (bottom-left, like a browser's link preview)
    def _build_answer(self):
        self.answer_panel = make_panel(NSMakeRect(0, 0, 10, 10),
                                       NSScreenSaverWindowLevel + 2, clickable=True)
        box = AnswerView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        box.setWantsLayer_(True)
        layer = box.layer()
        layer.setBackgroundColor_(NSColor.colorWithWhite_alpha_(0.0, 0.92).CGColor())
        layer.setBorderColor_(NSColor.colorWithWhite_alpha_(1.0, 0.15).CGColor())
        layer.setBorderWidth_(0.5)
        layer.setCornerRadius_(3.0)

        label = NSTextField.labelWithString_("")
        label.setFont_(NSFont.systemFontOfSize_(ANSWER_FONT_SIZE))
        label.setTextColor_(NSColor.whiteColor())
        label.setLineBreakMode_(NSLineBreakByTruncatingTail)
        label.setMaximumNumberOfLines_(1)
        box.addSubview_(label)

        self.answer_panel.setContentView_(box)
        self.answer_label = label
        self.answer_full = ""
        self.answer_display = ""
        self._flash_token = None

    def _show_answer(self, text, screen):
        self.answer_full = text                       # what gets copied
        self.answer_display = " ".join(text.split())  # one line on screen
        self._flash_token = None
        label = self.answer_label
        label.setStringValue_(self.answer_display)
        label.sizeToFit()
        tw = min(label.frame().size.width, ANSWER_MAX_WIDTH)
        th = label.frame().size.height
        padx, pady = 6, 2
        label.setFrame_(NSMakeRect(padx, pady, tw, th))

        f = screen.frame()
        self.answer_panel.setFrame_display_(
            NSMakeRect(f.origin.x + ANSWER_MARGIN, f.origin.y + ANSWER_MARGIN,
                       tw + 2 * padx, th + 2 * pady), True)
        self.answer_panel.orderFrontRegardless()

        if ANSWER_SECONDS:
            token = object()
            self._answer_token = token
            AppHelper.callLater(ANSWER_SECONDS, self._auto_hide, token)

    def copy_answer(self):
        if not self.answer_full:
            return
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(self.answer_full, NSPasteboardTypeString)
        # brief "Copied" confirmation, then the answer comes back
        token = object()
        self._flash_token = token
        self.answer_label.setStringValue_("Copied")
        AppHelper.callLater(0.8, self._end_flash, token)

    def _end_flash(self, token):
        if token is self._flash_token:
            self._flash_token = None
            self.answer_label.setStringValue_(self.answer_display)

    def _auto_hide(self, token):
        if token is self._answer_token:
            self._hide_answer()

    def _hide_answer(self):
        self._answer_token = None
        self.answer_panel.orderOut_(None)


# -------------------------------------------------------------------- main ---
def main():
    # Reads ANTHROPIC_API_KEY from the .env file next to this script.
    load_dotenv(Path(__file__).resolve().with_name(".env"))
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key.startswith("sk-ant-") or key == "sk-ant-your-key-here":
        print("No API key found. Copy .env.example to .env and paste your key "
              "into it (see README.md).")
        sys.exit(1)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon

    global CONTROLLER
    CONTROLLER = Controller()
    print("snapask running.  Ctrl+A: screenshot mode   Ctrl+S: cancel / hide   "
          "Ctrl+C here: quit")
    # Ctrl+C in the terminal quits right away (the Cocoa run loop would
    # otherwise never hand control back to Python's KeyboardInterrupt).
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()