"""Accessibility (global hotkeys) and Screen Recording (capture) permissions."""

import ctypes
import subprocess

import objc
import Quartz
from Foundation import NSDictionary

_AX = ctypes.cdll.LoadLibrary(
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
_AX.AXIsProcessTrusted.restype = ctypes.c_bool
_AX.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
_AX.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]

PANES = {
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "screen": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
}
NAMES = {"accessibility": "Accessibility", "screen": "Screen Recording"}


def has_accessibility():
    return bool(_AX.AXIsProcessTrusted())


def request_accessibility():
    """Adds the app to the Accessibility list and shows the system prompt."""
    opts = NSDictionary.dictionaryWithObject_forKey_(True, "AXTrustedCheckOptionPrompt")
    return bool(_AX.AXIsProcessTrustedWithOptions(objc.pyobjc_id(opts)))


def has_screen_recording():
    return bool(Quartz.CGPreflightScreenCaptureAccess())


def request_screen_recording():
    """Adds the app to the Screen Recording list (prompts once per app)."""
    return bool(Quartz.CGRequestScreenCaptureAccess())


def missing():
    out = []
    if not has_accessibility():
        out.append("accessibility")
    if not has_screen_recording():
        out.append("screen")
    return out


def open_settings(which):
    subprocess.Popen(["/usr/bin/open", PANES[which]])
