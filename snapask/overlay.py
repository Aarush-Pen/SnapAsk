"""Screenshot mode and the one-line answer bubble.

Selecting: the screen looks untouched (a fully transparent overlay on every
display), the cursor becomes a crosshair, and the only thing ever drawn is a
small black dot where the drag started. Release to capture, ⎋ or a tiny drag to
cancel. This module only reports the selected region; it knows nothing about
providers.
"""

import logging
import os

import objc
import Quartz
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSCursor,
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
    NSTrackingActiveAlways,
    NSTrackingArea,
    NSTrackingCursorUpdate,
    NSTrackingInVisibleRect,
    NSTrackingMouseMoved,
    NSView,
    NSWindowAnimationBehaviorNone,
    NSWorkspace,
)
from PyObjCTools import AppHelper

from .config import (ANSWER_FONT_SIZE, ANSWER_MARGIN, ANSWER_MAX_WIDTH, ANSWER_SECONDS,
                     DOT_DIAMETER, MIN_SELECTION)

log = logging.getLogger(__name__)

_PANEL_STYLE = 0 | (1 << 7)                 # borderless + non-activating
_PANEL_BEHAVIOR = (1 << 0) | (1 << 4) | (1 << 8)  # all Spaces, stationary,
                                                  # over full-screen apps
_COMPOSITE_COPY = 1                         # NSCompositingOperationCopy
_KEY_ESCAPE = 53
_HIDE_TIMEOUT = 0.5                         # max wait for the overlay to leave the screen

_OVERLAY = None                             # the views call back into this


def make_panel(rect, level, clickable, cls=NSPanel):
    p = cls.alloc().initWithContentRect_styleMask_backing_defer_(
        rect, _PANEL_STYLE, NSBackingStoreBuffered, False
    )
    p.setOpaque_(False)
    p.setBackgroundColor_(NSColor.clearColor())
    p.setHasShadow_(False)
    p.setAnimationBehavior_(NSWindowAnimationBehaviorNone)   # no fade in/out
    p.setLevel_(level)
    p.setCollectionBehavior_(_PANEL_BEHAVIOR)
    p.setHidesOnDeactivate_(False)
    p.setCanHide_(False)                    # stays up when the settings window hides the app
    p.setReleasedWhenClosed_(False)
    p.setIgnoresMouseEvents_(not clickable)
    return p


class SelectionPanel(NSPanel):
    """Borderless panels can't become key by default; this one must, so the
    crosshair cursor sticks and ⎋ arrives even without the hotkey listener."""

    def canBecomeKeyWindow(self):
        return True


class SelectionView(NSView):
    """Fully transparent view covering one screen. Catches the drag and draws
    nothing but the start dot."""

    def acceptsFirstMouse_(self, event):
        return True

    def acceptsFirstResponder(self):
        return True

    def mouseDown_(self, event):
        NSCursor.crosshairCursor().set()
        _OVERLAY.drag_began(NSEvent.mouseLocation())

    def mouseUp_(self, event):
        _OVERLAY.drag_ended(NSEvent.mouseLocation())

    def keyDown_(self, event):
        if event.keyCode() == _KEY_ESCAPE:
            _OVERLAY.cancel_selection()

    def cancelOperation_(self, sender):
        _OVERLAY.cancel_selection()

    # crosshair while armed
    def resetCursorRects(self):
        self.addCursorRect_cursor_(self.bounds(), NSCursor.crosshairCursor())

    def updateTrackingAreas(self):
        objc.super(SelectionView, self).updateTrackingAreas()
        for area in list(self.trackingAreas()):
            self.removeTrackingArea_(area)
        self.addTrackingArea_(NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(),
            NSTrackingCursorUpdate | NSTrackingMouseMoved | NSTrackingActiveAlways
            | NSTrackingInVisibleRect,
            self, None))

    # SnapAsk usually isn't the active app while selecting, so cursor rects
    # alone don't hold; re-assert the crosshair on every mouse event.
    def cursorUpdate_(self, event):
        NSCursor.crosshairCursor().set()

    def mouseMoved_(self, event):
        NSCursor.crosshairCursor().set()

    def mouseDragged_(self, event):
        NSCursor.crosshairCursor().set()

    def drawRect_(self, rect):
        NSColor.clearColor().set()
        NSRectFillUsingOperation(self.bounds(), _COMPOSITE_COPY)
        start = _OVERLAY.drag_start
        if start is None:
            return
        o = self.window().frame().origin
        r = DOT_DIAMETER / 2
        NSColor.blackColor().setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(start[0] - o.x - r, start[1] - o.y - r, DOT_DIAMETER, DOT_DIAMETER)).fill()


class AnswerView(NSView):
    """The answer bubble. Any click on it copies the full answer."""

    def acceptsFirstMouse_(self, event):
        return True

    def hitTest_(self, point):
        hit = objc.super(AnswerView, self).hitTest_(point)
        return self if hit is not None else None     # label clicks come here too

    def mouseDown_(self, event):
        _OVERLAY.copy_answer()


def screen_at(x, y):
    for s in NSScreen.screens():
        f = s.frame()
        if (f.origin.x <= x < f.origin.x + f.size.width
                and f.origin.y <= y < f.origin.y + f.size.height):
            return s
    return NSScreen.mainScreen()


def screen_with_mouse():
    p = NSEvent.mouseLocation()
    return screen_at(p.x, p.y)


def _on_screen_window_numbers():
    info = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly,
                                             Quartz.kCGNullWindowID) or []
    return {int(w["kCGWindowNumber"]) for w in info}


class Overlay:
    def __init__(self, on_region):
        """on_region(region, screen) gets the selection once the overlay is
        gone from the screen: region is (x, y, w, h) in points, top-left
        origin (may be fractional), ready for capture_region()."""
        global _OVERLAY
        _OVERLAY = self
        self.on_region = on_region
        self.armed = False
        self.overlays = []
        self.drag_start = None
        self._previous_app = None
        self._answer_token = None
        self._build_answer()

    # ---- screenshot mode
    def arm(self, activate=True):
        """Show the selection overlay. activate=False leaves the front app
        active (the crosshair then appears on the first mouse move); use it
        when activating SnapAsk would raise one of its windows over your work."""
        self.armed = True
        self.drag_start = None
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        self._previous_app = front if front and front.processIdentifier() != os.getpid() else None

        mouse_screen = screen_with_mouse()
        key_panel = None
        self.overlays = []
        for screen in NSScreen.screens():
            f = screen.frame()
            p = make_panel(f, NSScreenSaverWindowLevel, clickable=True, cls=SelectionPanel)
            view = SelectionView.alloc().initWithFrame_(NSMakeRect(0, 0, f.size.width, f.size.height))
            p.setContentView_(view)
            p.setAcceptsMouseMovedEvents_(True)
            p.orderFrontRegardless()
            self.overlays.append(p)
            if screen == mouse_screen:
                key_panel = p
        key_panel = key_panel or self.overlays[0]
        key_panel.setAcceptsMouseMovedEvents_(True)
        if activate:
            # Only the active app's cursor sticks; disarm() hands focus back.
            NSApp.activateIgnoringOtherApps_(True)
        key_panel.makeKeyAndOrderFront_(None)
        key_panel.makeFirstResponder_(key_panel.contentView())
        NSCursor.crosshairCursor().set()
        # The overlay needs a moment to be on screen with its tracking areas.
        for delay in (0.1, 0.3):
            AppHelper.callLater(delay, self._crosshair)

    def _crosshair(self):
        if self.armed:
            NSCursor.crosshairCursor().set()
            # macOS only re-evaluates the cursor when the mouse really moves
            # (zero-distance moves are dropped), so nudge it 1 pt and back;
            # without this the arrow stays until you move the mouse yourself.
            here = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
            for point in ((here.x + 1, here.y), (here.x, here.y)):
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(
                    None, Quartz.kCGEventMouseMoved, point, Quartz.kCGMouseButtonLeft))

    def disarm(self):
        """Remove the overlay and hand focus back to the app you were in."""
        self.armed = False
        self.drag_start = None
        for p in self.overlays:
            p.orderOut_(None)
        self.overlays = []
        NSCursor.arrowCursor().set()
        prev, self._previous_app = self._previous_app, None
        if prev is not None and not prev.isTerminated():
            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            if front is not None and front.processIdentifier() == os.getpid():
                prev.activateWithOptions_(0)

    def cancel_selection(self):
        if self.armed:
            log.info("selection cancelled")
            self.disarm()

    # ---- dragging (global Cocoa coords: origin bottom-left of main display)
    def drag_began(self, pt):
        self.drag_start = (pt.x, pt.y)
        for p in self.overlays:
            p.contentView().setNeedsDisplay_(True)

    def drag_ended(self, pt):
        if self.drag_start is None:
            return
        (x0, y0), (x1, y1) = self.drag_start, (pt.x, pt.y)
        x, y, w, h = min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)
        screen = screen_at(x0, y0)
        if w < MIN_SELECTION or h < MIN_SELECTION:       # a click or a tiny drag
            log.info("selection too small (%.1f×%.1f pt), cancelled", w, h)
            self.disarm()
            return
        primary_h = NSScreen.screens()[0].frame().size.height
        region = (x, primary_h - (y + h), w, h)          # top-left origin, in points

        numbers = {int(p.windowNumber()) for p in self.overlays}
        self.disarm()                                    # dot + overlay vanish
        self._when_hidden(numbers, lambda: self.on_region(region, screen), _HIDE_TIMEOUT)

    def _when_hidden(self, numbers, then, time_left):
        """Run `then` once the window server no longer shows the overlay
        windows, plus one more frame so nothing of them is composited."""
        if numbers & _on_screen_window_numbers() and time_left > 0:
            AppHelper.callLater(0.01, self._when_hidden, numbers, then, time_left - 0.01)
            return
        AppHelper.callLater(0.02, then)

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

    def show_answer(self, text, screen=None, seconds=ANSWER_SECONDS):
        """Show text bottom-left on `screen` (default: the one with the mouse).
        seconds=0 keeps it up until the next capture or cancel."""
        screen = screen or screen_with_mouse()
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

        token = object()
        self._answer_token = token
        if seconds:
            AppHelper.callLater(seconds, self._auto_hide, token)

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
            self.hide_answer()

    def hide_answer(self):
        self._answer_token = None
        self.answer_panel.orderOut_(None)

    @property
    def answer_visible(self):
        return bool(self.answer_panel.isVisible())

    def shutdown(self):
        self.disarm()
        self.hide_answer()
