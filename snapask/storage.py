"""API keys (macOS Keychain) and preferences (JSON in Application Support).

Keys never touch the preferences file, the logs, or the app bundle.
"""

import copy
import json
import logging
import os
import tempfile
from pathlib import Path

from keyring.backends import macOS as keychain_backend
from keyring.errors import KeyringError, PasswordDeleteError

from . import APP_NAME

log = logging.getLogger(__name__)

SUPPORT_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
PREFS_PATH = SUPPORT_DIR / "prefs.json"
LOG_DIR = Path.home() / "Library" / "Logs" / APP_NAME

KEYCHAIN_ACCOUNT = "api-key"


class KeyStore:
    """One Keychain item per provider: service "SnapAsk.<provider id>"."""

    def __init__(self):
        # Pinned rather than auto-detected: keyring finds backends through
        # package entry points, which a frozen .app doesn't always carry.
        self._kr = keychain_backend.Keyring()
        self._cache = {}

    @staticmethod
    def service(provider_id):
        return f"{APP_NAME}.{provider_id}"

    def get(self, provider_id):
        if provider_id not in self._cache:
            try:
                self._cache[provider_id] = self._kr.get_password(
                    self.service(provider_id), KEYCHAIN_ACCOUNT)
            except KeyringError:
                log.exception("keychain read failed for %s", provider_id)
                return None
        return self._cache[provider_id]

    def has(self, provider_id):
        return bool(self.get(provider_id))

    def set(self, provider_id, key):
        self._kr.set_password(self.service(provider_id), KEYCHAIN_ACCOUNT, key)
        self._cache[provider_id] = key

    def delete(self, provider_id):
        try:
            self._kr.delete_password(self.service(provider_id), KEYCHAIN_ACCOUNT)
        except PasswordDeleteError:
            pass                                    # already gone
        self._cache[provider_id] = None


def _provider_defaults():
    return {
        "model": None,              # chosen model id (None = provider default)
        "effort_level": None,       # last chosen level, e.g. "xhigh" (None = config default)
        "models": [],               # last live model list (ModelInfo dicts)
        "balance": {
            "start": None,          # balance entered from the console, USD
            "spent": 0.0,           # estimated spend since then
            "last_cost": None,      # cost of the most recent request
            "unpriced_model": None, # set when the last request had no price data
        },
    }


DEFAULTS = {
    "version": 1,
    "active_provider": "claude",
    "providers": {},
    "hotkeys": {},                  # action id -> {"key": keycode, "mods": [...]}
    "permissions_prompted": False,
}


class Prefs:
    def __init__(self, path=PREFS_PATH):
        self.path = Path(path)
        self.first_run = not self.path.exists()
        self.data = copy.deepcopy(DEFAULTS)
        if not self.first_run:
            try:
                with open(self.path) as f:
                    stored = json.load(f)
                self.data.update(stored)
            except (OSError, ValueError):
                log.exception("couldn't read %s; starting fresh", self.path)

    def provider(self, provider_id):
        p = self.data["providers"].setdefault(provider_id, _provider_defaults())
        p.pop("effort", None)                         # old per-model Off/Low/Medium/High map
        for k, v in _provider_defaults().items():     # fill keys added later
            p.setdefault(k, v)
        for k, v in _provider_defaults()["balance"].items():
            p["balance"].setdefault(k, v)
        return p

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".prefs-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError:
            log.exception("couldn't save preferences")
            try:
                os.remove(tmp)
            except OSError:
                pass
