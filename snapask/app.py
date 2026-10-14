"""App controller: wires hotkeys, the overlay, providers, storage and the UI.

All state changes happen here, on the main thread, and end with refresh(),
which re-renders the settings window from a fresh snapshot.
"""

import fcntl
import logging
import logging.handlers
import os
import signal
import sys
import threading

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAlertSecondButtonReturn,
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSEventModifierFlagCommand,
    NSEventModifierFlagShift,
    NSMenu,
    NSMenuItem,
    NSTerminateNow,
    NSWorkspace,
)
from Foundation import (NSURL, NSDistributedNotificationCenter, NSObject, NSRunLoop,
                        NSRunLoopCommonModes, NSTimer)
from PyObjCTools import AppHelper

from . import APP_NAME, BUNDLE_ID, __version__, config, effort_capabilities, permissions
from .capture import capture_region
from .hotkeys import ACTIONS, Combo, HotkeyManager
from .overlay import Overlay
from .providers import ORDER, PROVIDERS, ErrorKind, Job, ModelInfo, ProviderError
from .storage import LOG_DIR, SUPPORT_DIR, KeyStore, Prefs, _provider_defaults
from .ui.settings_window import SettingsWindowController, activate_app, money
from .ui.status_item import StatusItemController

log = logging.getLogger("snapask")

SHOW_NOTIFICATION = BUNDLE_ID + ".show-settings"   # a second launch asks the first to open


class SnapAskApp(NSObject):
    # --- lifecycle ------------------------------------------------------------
    def applicationDidFinishLaunching_(self, note):
        self._shut_down = False
        self.prefs = Prefs()
        self.keys = KeyStore()
        stored = self.prefs.data.get("active_provider")
        self.active = stored if stored in PROVIDERS else ORDER[0]
        self.job = None
        self.workers = set()
        self.key_state = {pid: {"busy": False, "error": None, "token": None} for pid in ORDER}
        self.recording = None
        self.hotkey_error = None
        self.clear_key_field = False
        self.missing = permissions.missing()
        self._perm_timer = None

        self._install_main_menu()
        self.overlay = Overlay(self.on_region)
        self.hotkeys = HotkeyManager(self.handle_action, wants_escape=lambda: self.overlay.armed)
        self.hotkeys.bindings.update(self._stored_bindings())
        self.window = SettingsWindowController.alloc().initWithApp_(self)
        self.status = StatusItemController.alloc().initWithApp_(self)

        if not self.hotkeys.start():
            log.warning("no Accessibility permission yet; hotkeys are off until it's granted")
        if self.missing:
            self._watch_permissions()
        self.refresh()
        for pid in ORDER:
            if self.keys.has(pid):
                self._refresh_models(pid)

        NSDistributedNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self, "showSettings:", SHOW_NOTIFICATION, None)
        first_run = self.prefs.first_run
        self.prefs.save()
        log.info("%s %s started (provider %s, missing permissions: %s)",
                 APP_NAME, __version__, self.active, self.missing or "none")
        if first_run:
            self.show_window()
        AppHelper.callLater(0.3, self._maybe_prompt_permissions)

    def applicationSupportsSecureRestorableState_(self, app):
        return True

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, has_windows):
        self.show_window()                 # opening the app again shows settings
        return False

    def applicationShouldTerminate_(self, sender):
        self.shutdown()
        return NSTerminateNow

    @objc.python_method
    def quit(self):
        """Quit button, menu bar Quit and ⌘Q all end up here (via terminate:)."""
        NSApp.terminate_(None)

    @objc.python_method
    def shutdown(self):
        if self._shut_down:
            return
        self._shut_down = True
        log.info("quitting: stopping hotkeys, request, timers and menu bar icon")
        self.hotkeys.stop()
        self._cancel_job()
        if self._perm_timer is not None:
            self._perm_timer.invalidate()
            self._perm_timer = None
        self.overlay.shutdown()
        self.status.remove()
        self.prefs.data["active_provider"] = self.active
        self.prefs.save()
        for t in list(self.workers):
            t.join(timeout=1.0)
        alive = [t.name for t in self.workers if t.is_alive()]
        log.info("quit complete%s", f" ({len(alive)} worker(s) abandoned)" if alive else "")
        for handler in logging.getLogger().handlers:
            handler.flush()

    # --- hotkey actions -------------------------------------------------------
    @objc.python_method
    def handle_action(self, action):
        log.info("hotkey %s", action)
        if action == "capture":
            self.capture()
        elif action == "cancel":
            self.cancel()
        elif action == "escape":
            self.overlay.cancel_selection()
        elif action == "toggle_window":
            self.toggle_window()
        elif action == "cycle_provider":
            nxt = ORDER[(ORDER.index(self.active) + 1) % len(ORDER)]
            self.set_active_provider(nxt, announce=True)

    @objc.python_method
    def capture(self):
        if self.overlay.armed:
            self.overlay.disarm()
            return
        self._cancel_job()
        self.overlay.hide_answer()
        prov = PROVIDERS[self.active]
        if not self.keys.has(self.active):
            self.toast(f"Add your {prov.name} API key in settings")
            self.show_window()
            return
        if not permissions.has_screen_recording():
            self.toast(f"Allow Screen Recording for {APP_NAME} in System Settings")
            self.show_window()
            return
        # Activating SnapAsk (for an instant crosshair) would pull a settings
        # window that's open behind other apps in front of them.
        settings_in_background = self.window.is_visible() and not NSApp.isActive()
        self.overlay.arm(activate=not settings_in_background)

    @objc.python_method
    def cancel(self):
        self._cancel_job()
        self.overlay.hide_answer()
        if self.overlay.armed:
            self.overlay.disarm()

    @objc.python_method
    def toast(self, text):
        self.overlay.show_answer(text, seconds=config.TOAST_SECONDS)

    # --- the request ----------------------------------------------------------
    @objc.python_method
    def on_region(self, region, screen):
        pid = self.active
        prov, key = PROVIDERS[pid], self.keys.get(pid)
        model = self.current_model(pid)
        effort = self.current_effort(pid, model)
        job = Job()
        self.job = job
        t = threading.Thread(target=self._worker, name="snapask-request", daemon=True,
                             args=(job, prov, key, model, effort, self.models(pid), region, screen))
        self.workers.add(t)
        t.start()

    @objc.python_method
    def _worker(self, job, prov, key, model, effort, known, region, screen):
        result = error = None
        try:
            try:
                png = capture_region(*region)
            except Exception as e:
                log.exception("screenshot failed")
                raise ProviderError(ErrorKind.OTHER, "Couldn't take the screenshot") from e
            if not job.cancelled.is_set():
                result = prov.send(png, config.USER_PROMPT, model, effort, key=key,
                                   system=config.SYSTEM_PROMPT, job=job, known=known)
        except ProviderError as e:
            error = e
        except Exception:
            log.exception("request failed")
            error = ProviderError(ErrorKind.OTHER, "Something went wrong (details in the log)")
        AppHelper.callAfter(self._job_finished, job, prov.id, model, result, error, screen,
                            threading.current_thread())

    @objc.python_method
    def _job_finished(self, job, pid, model, result, error, screen, thread):
        self.workers.discard(thread)
        usage = result.usage if result is not None else getattr(error, "usage", None)
        if usage is not None:
            self._charge(pid, model, usage)
        if job is not self.job or job.cancelled.is_set():
            self.refresh()
            return                                       # cancelled: show nothing
        self.job = None
        if error is not None:
            log.warning("request error provider=%s kind=%s: %s", pid, error.kind.value, error.message)
            if error.kind == ErrorKind.AUTH:
                self.key_state[pid]["error"] = f"{PROVIDERS[pid].vendor} rejected this key. Paste a new one."
            self.overlay.show_answer("⚠ " + error.message, screen)
        elif result is not None:
            log.info("answer provider=%s model=%s chars=%d", pid, result.model, len(result.text))
            self.overlay.show_answer(result.text, screen)
        self.refresh()

    @objc.python_method
    def _cancel_job(self):
        if self.job is not None:
            self.job.cancel()
            self.job = None

    @objc.python_method
    def _charge(self, pid, model, usage):
        if not (usage.input_tokens or usage.cached_tokens or usage.output_tokens or usage.attempts):
            return
        cost = PROVIDERS[pid].cost_from_usage(usage)
        b = self.prefs.provider(pid)["balance"]
        if cost is None:
            b["last_cost"] = None
            b["unpriced_model"] = usage.model or model
        else:
            b["spent"] += cost
            b["last_cost"] = cost
            b["unpriced_model"] = None
        self.prefs.save()
        log.info("usage provider=%s model=%s %s cost=%s", pid, usage.model or model,
                 usage.summary(), "unknown" if cost is None else f"{cost:.6f}")

    # --- providers, models, effort -------------------------------------------
    @objc.python_method
    def shown_provider(self):
        return self.active

    @objc.python_method
    def set_active_provider(self, pid, announce=False):
        if pid not in PROVIDERS:
            return
        if pid != self.active:
            self.cancel_recording()
            old = self.key_state[self.active]
            if old.get("error_from_save"):          # a failed paste doesn't follow you around
                old.update(error=None, error_from_save=False)
            self.active = pid
            self.prefs.data["active_provider"] = pid
            self.prefs.save()
            self.clear_key_field = True
            log.info("active provider -> %s", pid)
        if announce:
            self.toast(f"Provider: {PROVIDERS[pid].name}")
        self.refresh()

    @objc.python_method
    def models(self, pid):
        cached = self.prefs.provider(pid)["models"]
        if cached:
            return [ModelInfo.from_json(d) for d in cached]
        return list(PROVIDERS[pid].fallback_models)

    @objc.python_method
    def current_model(self, pid):
        ids = [m.id for m in self.models(pid)]
        chosen = self.prefs.provider(pid)["model"]
        if chosen in ids:
            return chosen
        default = PROVIDERS[pid].default_model
        return default if default in ids or not ids else ids[0]

    @objc.python_method
    def effort_spec(self, pid, model):
        """effort_capabilities.Effort for the model, or None if it isn't in the table."""
        return PROVIDERS[pid].effort_spec(model, self.models(pid))

    @objc.python_method
    def current_effort(self, pid, model):
        """The saved level if this model accepts it, else the closest one it does."""
        spec = self.effort_spec(pid, model)
        if spec is None or not spec.levels:
            return None
        saved = self.prefs.provider(pid)["effort_level"] or config.DEFAULT_EFFORT
        return effort_capabilities.snap(saved, spec.levels)

    @objc.python_method
    def _keep_effort_valid(self, pid):
        """After a model change: if the saved level doesn't fit the new model,
        save the level it snapped to (models without effort leave it alone)."""
        level = self.current_effort(pid, self.current_model(pid))
        p = self.prefs.provider(pid)
        if level is not None and level != p["effort_level"]:
            log.info("effort %s: %s -> %s for %s", pid, p["effort_level"], level,
                     self.current_model(pid))
            p["effort_level"] = level
            self.prefs.save()

    @objc.python_method
    def set_model(self, pid, model):
        self.prefs.provider(pid)["model"] = model
        self.prefs.save()
        self._keep_effort_valid(pid)
        log.info("model %s -> %s (effort %s)", pid, model, self.current_effort(pid, model))
        self.refresh()

    @objc.python_method
    def set_effort(self, pid, level):
        spec = self.effort_spec(pid, self.current_model(pid))
        if spec is not None and level in spec.levels:
            self.prefs.provider(pid)["effort_level"] = level
            self.prefs.save()
            log.info("effort %s -> %s", pid, level)
        self.refresh()

    @objc.python_method
    def _refresh_models(self, pid):
        """Background: reload the live model list (also re-checks the saved key)."""
        key, prov = self.keys.get(pid), PROVIDERS[pid]

        def work():
            try:
                models, err = prov.list_models(key), None
            except ProviderError as e:
                models, err = None, e
            except Exception as e:
                log.exception("model list failed for %s", pid)
                models, err = None, ProviderError(ErrorKind.OTHER, str(e))
            AppHelper.callAfter(self._models_loaded, pid, key, models, err,
                                threading.current_thread())
        self._spawn(work, f"snapask-models-{pid}")

    @objc.python_method
    def _models_loaded(self, pid, key, models, err, thread):
        self.workers.discard(thread)
        if self.keys.get(pid) != key:
            return                                        # key changed meanwhile
        if models:
            self.prefs.provider(pid)["models"] = [m.to_json() for m in models]
            self.prefs.save()
            self._keep_effort_valid(pid)
        elif err is not None:
            log.warning("model list for %s failed: %s", pid, err.message)
            if err.kind == ErrorKind.AUTH:
                self.key_state[pid]["error"] = (
                    f"{PROVIDERS[pid].vendor} rejected your saved key. Paste a new one.")
        self.refresh()

    @objc.python_method
    def _spawn(self, fn, name):
        t = threading.Thread(target=fn, name=name, daemon=True)
        self.workers.add(t)
        t.start()

    # --- API keys -------------------------------------------------------------
    @objc.python_method
    def save_key(self, pid, key):
        st = self.key_state[pid]
        if st["busy"]:
            return
        token = object()
        st.update(busy=True, error=None, error_from_save=False, token=token)
        self.refresh()
        prov = PROVIDERS[pid]

        def work():
            try:
                models, err = prov.validate_key(key), None
            except ProviderError as e:
                models, err = None, e
            except Exception:
                log.exception("key check failed for %s", pid)
                models, err = None, ProviderError(ErrorKind.OTHER, "unexpected error")
            AppHelper.callAfter(self._key_checked, pid, key, token, models, err,
                                threading.current_thread())
        self._spawn(work, f"snapask-keycheck-{pid}")

    @objc.python_method
    def _key_checked(self, pid, key, token, models, err, thread):
        self.workers.discard(thread)
        st, prov = self.key_state[pid], PROVIDERS[pid]
        if st["token"] is not token:
            return                                        # superseded or deleted
        st.update(busy=False, token=None)
        if err is not None:
            log.info("key for %s not saved: %s", pid, err.kind.value)
            if err.kind == ErrorKind.AUTH:
                st["error"] = f"{prov.vendor} rejected that key. Not saved."
            elif err.kind == ErrorKind.NETWORK:
                st["error"] = f"Couldn't reach {prov.vendor}. Not saved."
            else:
                st["error"] = f"Not saved: {err.message}"
            st["error_from_save"] = True
            self.refresh()
            return
        try:
            self.keys.set(pid, key)
        except Exception:
            log.exception("keychain write failed")
            st["error"] = "Couldn't save the key to the Keychain."
            self.refresh()
            return
        st["error"] = None
        self.prefs.provider(pid)["models"] = [m.to_json() for m in models]
        self.prefs.save()
        self._keep_effort_valid(pid)
        self.clear_key_field = True
        log.info("saved %s key to the Keychain (%d image models)", pid, len(models))
        self.refresh()

    @objc.python_method
    def delete_key(self, pid):
        self.keys.delete(pid)
        p = self.prefs.provider(pid)
        p["balance"] = _provider_defaults()["balance"]
        p["models"] = []
        self.key_state[pid].update(busy=False, error=None, token=None)
        self.prefs.save()
        self.clear_key_field = True
        log.info("deleted %s key and its tracked balance", pid)
        self.refresh()

    # --- balance --------------------------------------------------------------
    @objc.python_method
    def set_starting_balance(self, pid, value):
        b = self.prefs.provider(pid)["balance"]
        b.update(start=round(value, 2), spent=0.0, last_cost=None, unpriced_model=None)
        self.prefs.save()
        log.info("starting balance %s -> %.2f", pid, value)
        self.refresh()

    @objc.python_method
    def reset_balance(self, pid):
        self.prefs.provider(pid)["balance"] = _provider_defaults()["balance"]
        self.prefs.save()
        log.info("balance reset for %s", pid)
        self.refresh()

    # --- shortcuts ------------------------------------------------------------
    @objc.python_method
    def _stored_bindings(self):
        out = {}
        for action_id, d in self.prefs.data.get("hotkeys", {}).items():
            try:
                combo = Combo.from_json(d)
            except (KeyError, TypeError, ValueError):
                continue
            if action_id in self.hotkeys.bindings and combo.is_valid:
                out[action_id] = combo
        return out

    @objc.python_method
    def begin_recording(self, action_id):
        if self.recording == action_id:
            self.cancel_recording()
            return
        if not self.hotkeys.running:
            self.hotkey_error = "Grant Accessibility permission first"
            self.refresh()
            return
        self.recording, self.hotkey_error = action_id, None
        self.hotkeys.record(self._recorded, self._record_invalid)
        self.refresh()

    @objc.python_method
    def _recorded(self, combo):
        action_id, self.recording = self.recording, None
        if combo is None or action_id is None:
            self.refresh()
            return
        clash = next((a for a in ACTIONS if a.id != action_id
                      and self.hotkeys.bindings[a.id] == combo), None)
        if clash is not None:
            self.hotkey_error = f"{combo.label} is already “{clash.title}”"
        else:
            self.hotkeys.bindings[action_id] = combo
            self.prefs.data["hotkeys"][action_id] = combo.to_json()
            self.prefs.save()
            self.hotkey_error = None
            log.info("shortcut %s -> %s", action_id, combo.label)
        self.refresh()

    @objc.python_method
    def _record_invalid(self, combo):
        self.hotkey_error = "Use ⌃ or ⌥ with a key (or ⌘ plus another modifier)"
        self.refresh()

    @objc.python_method
    def cancel_recording(self):
        if self.recording is not None:
            self.hotkeys.stop_recording()
            self.recording = None
            self.hotkey_error = None
            self.refresh()

    @objc.python_method
    def restore_default_hotkeys(self):
        self.cancel_recording()
        self.hotkeys.bindings.update({a.id: a.default for a in ACTIONS})
        self.prefs.data["hotkeys"] = {}
        self.prefs.save()
        self.hotkey_error = None
        self.refresh()

    # --- permissions ----------------------------------------------------------
    @objc.python_method
    def _watch_permissions(self):
        if self._perm_timer is not None:
            return
        self._perm_timer = NSTimer.timerWithTimeInterval_repeats_block_(
            2.0, True, lambda t: self._check_permissions())
        NSRunLoop.mainRunLoop().addTimer_forMode_(self._perm_timer, NSRunLoopCommonModes)

    @objc.python_method
    def _check_permissions(self):
        missing = permissions.missing()
        if "accessibility" not in missing and not self.hotkeys.running:
            self.hotkeys.start()
        if missing != self.missing:
            log.info("permissions changed, missing: %s", missing or "none")
            self.missing = missing
            self.refresh()
        if not missing and self._perm_timer is not None:
            self._perm_timer.invalidate()
            self._perm_timer = None

    @objc.python_method
    def _maybe_prompt_permissions(self):
        """One-time explanation on first launch, with buttons to the right panes."""
        missing = permissions.missing()
        if not missing or self.prefs.data.get("permissions_prompted"):
            return
        self.prefs.data["permissions_prompted"] = True
        self.prefs.save()
        why = {"accessibility": "Accessibility, so the keyboard shortcuts work in every app",
               "screen": "Screen Recording, to capture the area you select"}
        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"{APP_NAME} needs {len(missing)} permission"
                              + ("s" if len(missing) > 1 else ""))
        alert.setInformativeText_(
            "\n".join("• " + why[m] for m in missing)
            + f"\n\nTurn {APP_NAME} on in System Settings › Privacy & Security. "
            f"If it still doesn't work, quit and reopen {APP_NAME}.")
        for m in missing:
            alert.addButtonWithTitle_(f"Open {permissions.NAMES[m]} Settings")
        alert.addButtonWithTitle_("Later")
        activate_app()
        response = alert.runModal()
        buttons = [NSAlertFirstButtonReturn, NSAlertSecondButtonReturn]
        for m, b in zip(missing, buttons):
            if response == b:
                self._request_and_open(m)

    @objc.python_method
    def _request_and_open(self, which):
        # Asking first puts SnapAsk in the list, so there's a switch to flip.
        if which == "accessibility":
            permissions.request_accessibility()
        else:
            permissions.request_screen_recording()
        permissions.open_settings(which)

    @objc.python_method
    def open_permission_settings(self):
        missing = permissions.missing()
        if missing:
            self._request_and_open(missing[0])

    # --- window & menus -------------------------------------------------------
    @objc.python_method
    def show_window(self):
        self.window.show()

    @objc.python_method
    def toggle_window(self):
        self.window.toggle()

    def showSettings_(self, sender):
        self.show_window()

    @objc.python_method
    def open_url(self, url):
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))

    @objc.python_method
    def view_state(self):
        pid = self.active
        prov, p, st = PROVIDERS[pid], self.prefs.provider(pid), self.key_state[pid]
        model = self.current_model(pid)
        models = self.models(pid)
        if model not in [m.id for m in models]:
            models.append(prov.model_info(model, models))
        return {
            "provider": pid,
            "has_key": self.keys.has(pid),
            "masked_key": prov.mask_key(self.keys.get(pid)),
            "key_busy": st["busy"],
            "key_error": st["error"],
            "clear_key_field": self.clear_key_field,
            "balance": dict(p["balance"]),
            "models": models,
            "models_live": bool(p["models"]),
            "model": model,
            "effort_spec": self.effort_spec(pid, model),
            "effort": self.current_effort(pid, model),
            "hotkeys": dict(self.hotkeys.bindings),
            "recording": self.recording,
            "hotkey_error": self.hotkey_error,
            "hotkeys_customized": any(self.hotkeys.bindings[a.id] != a.default for a in ACTIONS),
            "missing_permissions": [permissions.NAMES[m] for m in self.missing],
        }

    @objc.python_method
    def refresh(self):
        self.window.render(self.view_state())
        self.clear_key_field = False

    @objc.python_method
    def menu_state(self):
        pid, prov = self.active, PROVIDERS[self.active]
        b = self.prefs.provider(pid)["balance"]
        if not self.keys.has(pid):
            summary = f"{prov.name}: API key needed"
        elif b["start"] is None:
            summary = f"{prov.name}: no balance set"
        else:
            summary = f"{prov.name}: {money(b['start'] - b['spent'])} remaining"
        return {"active": pid, "keys": {p: self.keys.has(p) for p in ORDER},
                "summary": summary, "capture_combo": self.hotkeys.bindings["capture"]}

    @objc.python_method
    def _install_main_menu(self):
        """Invisible for a menu bar app, but it's what makes ⌘Q, ⌘W and
        copy/paste in the key field work while the window is focused."""
        bar = NSMenu.alloc().init()

        def submenu(title, items):
            holder = NSMenuItem.alloc().init()
            menu = NSMenu.alloc().initWithTitle_(title)
            for entry in items:
                if entry is None:
                    menu.addItem_(NSMenuItem.separatorItem())
                    continue
                name, action, key, *extra = entry
                item = menu.addItemWithTitle_action_keyEquivalent_(name, action, key)
                if extra:
                    item.setKeyEquivalentModifierMask_(extra[0])
                if action == "showSettings:":
                    item.setTarget_(self)
            holder.setSubmenu_(menu)
            bar.addItem_(holder)

        submenu(APP_NAME, [("Settings…", "showSettings:", ","), None,
                           (f"Quit {APP_NAME}", "terminate:", "q")])
        submenu("Edit", [("Undo", "undo:", "z"),
                         ("Redo", "redo:", "z", NSEventModifierFlagCommand | NSEventModifierFlagShift),
                         None, ("Cut", "cut:", "x"), ("Copy", "copy:", "c"),
                         ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")])
        submenu("Window", [("Close", "performClose:", "w"),
                           ("Minimize", "performMiniaturize:", "m")])
        NSApp.setMainMenu_(bar)


class _LogStream:
    """stderr for the packaged app (which has no console): tracebacks from
    Cocoa callbacks end up in the log instead of nowhere."""

    def __init__(self, logger, level):
        self.logger, self.level, self._buf = logger, level, ""

    def write(self, text):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self.logger.log(self.level, line)
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "snapask.log", maxBytes=1_000_000, backupCount=2)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    if getattr(sys, "frozen", False):
        sys.stderr = _LogStream(logging.getLogger("stderr"), logging.ERROR)
        sys.stdout = _LogStream(logging.getLogger("stdout"), logging.INFO)
    elif sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)
    # SDK and HTTP chatter stays out (it can include request details).
    for name in ("httpx", "httpx2", "httpcore", "httpcore2", "anthropic", "openai",
                 "google_genai", "google"):
        logging.getLogger(name).setLevel(logging.WARNING)


_DELEGATE = None
_LOCK = None


def _single_instance():
    """True if we're the only SnapAsk running (two would fight over the hotkeys)."""
    global _LOCK
    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    _LOCK = os.open(SUPPORT_DIR / "running.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)   # released when the process exits
    except BlockingIOError:
        return False
    return True


def main():
    global _DELEGATE
    setup_logging()
    if not _single_instance():
        log.info("%s is already running; asking it to show its window", APP_NAME)
        NSDistributedNotificationCenter.defaultCenter().postNotificationName_object_userInfo_deliverImmediately_(
            SHOW_NOTIFICATION, None, None, True)
        sys.exit(0)
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # menu bar only, no Dock icon
    _DELEGATE = SnapAskApp.alloc().init()
    app.setDelegate_(_DELEGATE)
    # Ctrl+C in a terminal quits right away, as before.
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    AppHelper.runEventLoop()
