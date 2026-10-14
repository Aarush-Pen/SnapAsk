"""Unit tests for the effort capability table and how it's used.

    .venv/bin/python -m unittest discover -s tests -v      (from the repo root)
"""

import tempfile
import unittest
from pathlib import Path

from snapask import effort_capabilities as caps
from snapask.providers import PROVIDERS, ModelInfo

ALL5 = ("low", "medium", "high", "xhigh", "max")

# What the docs say (2026-10-14), written out independently of the table.
EXPECTED = {
    "claude": {
        "claude-fable-5-1": (ALL5, "high"),
        "claude-fable-5": (ALL5, "high"),
        "claude-opus-5-5": (ALL5, "medium"),
        "claude-opus-5": (ALL5, "high"),
        "claude-opus-4-8": (ALL5, "high"),
        "claude-opus-4-7": (ALL5, "high"),
        "claude-opus-4-6": (("low", "medium", "high", "max"), "high"),
        "claude-opus-4-5": (("low", "medium", "high"), "high"),
        "claude-sonnet-5-5": (ALL5, "high"),
        "claude-sonnet-5": (ALL5, "high"),
        "claude-sonnet-4-6": (("low", "medium", "high", "max"), "high"),
        "claude-sonnet-4-5": ((), None),
        "claude-haiku-5-5": (ALL5, "medium"),
        "claude-haiku-4-5": ((), None),
    },
    "openai": {
        "gpt-6-astra": (ALL5, None),
        "gpt-6.1-sol": (ALL5, "medium"),
        "gpt-6-sol": (("none",) + ALL5, "medium"),
        "gpt-6-luna": (("none",) + ALL5, "medium"),
        "gpt-5.6-sol": (("none",) + ALL5, "medium"),
        "gpt-5.6-terra": (("none",) + ALL5, "medium"),
        "gpt-5.6-luna": (("none",) + ALL5, "medium"),
        "gpt-5.5": (("none", "low", "medium", "high", "xhigh"), "medium"),
        "gpt-5.4": (("none", "low", "medium", "high", "xhigh"), "none"),
        "gpt-5.4-mini": (("none", "low", "medium", "high", "xhigh"), "none"),
        "gpt-5.4-nano": (("none", "low", "medium", "high", "xhigh"), "none"),
        "gpt-5.2": (("none", "low", "medium", "high", "xhigh"), "none"),
        "gpt-5.1": (("none", "low", "medium", "high"), "none"),
        "gpt-5": (("minimal", "low", "medium", "high"), None),
        "gpt-4.1": ((), None),
        "gpt-4.1-mini": ((), None),
        "gpt-4.1-nano": ((), None),
        "gpt-4o": ((), None),
        "gpt-4o-mini": ((), None),
    },
}


class TableTests(unittest.TestCase):
    def test_every_model_matches_the_docs(self):
        for pid, models in EXPECTED.items():
            self.assertEqual(set(models), set(caps.TABLES[pid]), f"{pid} table has extra/missing ids")
            for model, (levels, default) in models.items():
                with self.subTest(model=model):
                    spec = caps.lookup(pid, model)
                    self.assertEqual(spec.levels, levels)
                    self.assertEqual(spec.default, default)

    def test_levels_are_in_order_and_defaults_are_offered(self):
        for pid, table in caps.TABLES.items():
            for model, spec in table.items():
                with self.subTest(model=model):
                    ranks = [caps.ORDER.index(lv) for lv in spec.levels]
                    self.assertEqual(ranks, sorted(ranks))
                    if spec.default is not None:
                        self.assertIn(spec.default, spec.levels)

    def test_dated_snapshots_resolve_to_their_base_model(self):
        self.assertEqual(caps.lookup("claude", "claude-opus-4-5-20251101").levels, ("low", "medium", "high"))
        self.assertEqual(caps.lookup("claude", "claude-haiku-4-5-20251001"), caps.NOT_SUPPORTED)
        self.assertEqual(caps.lookup("claude", "claude-sonnet-4-5-20250929"), caps.NOT_SUPPORTED)
        self.assertEqual(caps.lookup("openai", "gpt-5.4-2026-03-05").default, "none")
        self.assertEqual(caps.lookup("openai", "gpt-4o-2024-08-06"), caps.NOT_SUPPORTED)
        self.assertEqual(caps.lookup("openai", "gpt-6.1-sol-2026-08-01").levels, ALL5)

    def test_no_loose_matching_across_families(self):
        for pid, model in [("openai", "gpt-5.4-pro"), ("openai", "gpt-5-mini"),
                           ("openai", "gpt-5-nano"), ("openai", "o3"), ("openai", "o4-mini"),
                           ("openai", "gpt-6.2-sol"), ("openai", "gpt-5.4-mini-high"),
                           ("claude", "claude-opus-4-9"), ("claude", "claude-opus-5-5-fast"),
                           ("claude", "claude-haiku-6")]:
            with self.subTest(model=model):
                self.assertIsNone(caps.lookup(pid, model))

    def test_gemini_keeps_family_matching(self):
        self.assertEqual(caps.lookup("gemini", "gemini-3.1-pro-preview").param, "level")
        self.assertEqual(caps.lookup("gemini", "gemini-2.5-flash-lite").levels, ("off", "low", "medium", "high"))
        self.assertEqual(caps.lookup("gemini", "models/gemini-2.5-pro").param, "budget")


class SnapTests(unittest.TestCase):
    def test_keeps_a_supported_level(self):
        self.assertEqual(caps.snap("xhigh", ALL5), "xhigh")

    def test_prefers_the_next_lower_level(self):
        self.assertEqual(caps.snap("xhigh", ("low", "medium", "high", "max")), "high")
        self.assertEqual(caps.snap("max", ("none", "low", "medium", "high", "xhigh")), "xhigh")
        self.assertEqual(caps.snap("minimal", ("none", "low", "medium", "high")), "none")
        self.assertEqual(caps.snap("medium", ("low", "high")), "low")

    def test_goes_up_when_nothing_is_lower(self):
        self.assertEqual(caps.snap("none", ALL5), "low")
        self.assertEqual(caps.snap("off", ("minimal", "low")), "minimal")

    def test_no_levels(self):
        self.assertIsNone(caps.snap("high", ()))


class RequestTests(unittest.TestCase):
    """What actually goes on the wire for each provider."""

    def claude(self, model, effort, adaptive=True):
        p = PROVIDERS["claude"]
        return p.request_settings(ModelInfo(model, meta={"adaptive": adaptive}), effort)

    def test_claude_sends_exact_level_with_adaptive_thinking(self):
        s = self.claude("claude-opus-4-6", "max")
        self.assertEqual(s["output_config"], {"effort": "max"})
        self.assertEqual(s["thinking"], {"type": "adaptive"})

    def test_claude_never_sends_an_unlisted_level(self):
        self.assertNotIn("output_config", self.claude("claude-opus-4-6", "xhigh"))
        self.assertNotIn("output_config", self.claude("claude-haiku-4-5-20251001", "high", False))
        self.assertNotIn("output_config", self.claude("claude-opus-9", "high"))

    def test_claude_without_adaptive_thinking(self):
        s = self.claude("claude-opus-4-5-20251101", "medium", adaptive=False)
        self.assertEqual(s["output_config"], {"effort": "medium"})
        self.assertNotIn("thinking", s)

    def test_claude_never_disables_thinking(self):
        for model in caps.CLAUDE:
            for level in caps.CLAUDE[model].levels:
                self.assertNotEqual(self.claude(model, level).get("thinking"), {"type": "disabled"})

    def test_openai(self):
        p = PROVIDERS["openai"]
        send = lambda m, e: p.request_settings(ModelInfo(m), e)
        self.assertEqual(send("gpt-6-luna", "none")["reasoning"], {"effort": "none"})
        self.assertEqual(send("gpt-5", "minimal")["reasoning"], {"effort": "minimal"})
        self.assertNotIn("reasoning", send("gpt-6.1-sol", "none"))     # not accepted
        self.assertNotIn("reasoning", send("gpt-4.1", "high"))         # non-reasoning
        self.assertNotIn("reasoning", send("o3", "high"))              # unknown: send nothing
        for model in ("gpt-6-astra", "gpt-4o"):
            s = send(model, "high")
            self.assertFalse({"temperature", "top_p"} & set(s))

    def test_gemini(self):
        p = PROVIDERS["gemini"]
        send = lambda m, e, thinking=None: p.request_settings(ModelInfo(m, meta={"thinking": thinking}), e)
        self.assertEqual(send("gemini-3.6-flash", "minimal")["thinking_config"], {"thinking_level": "MINIMAL"})
        self.assertEqual(send("gemini-2.5-flash", "off")["thinking_config"], {"thinking_budget": 0})
        self.assertEqual(send("gemini-3-pro-preview", "medium"), {})
        # unlisted thinking model keeps the old low/high fallback
        known = [ModelInfo("gemini-9-mystery", meta={"thinking": True})]
        self.assertEqual(p.effort_spec("gemini-9-mystery", known).levels, ("low", "high"))
        self.assertIsNone(p.effort_spec("gemini-9-mystery"))


class SwitchingTests(unittest.TestCase):
    """The app keeps the saved level when it fits, else snaps and saves."""

    def setUp(self):
        from snapask.app import SnapAskApp
        from snapask.storage import Prefs
        self.tmp = tempfile.TemporaryDirectory()
        app = SnapAskApp.alloc().init()
        app.prefs = Prefs(Path(self.tmp.name) / "prefs.json")
        self.app = app

    def tearDown(self):
        self.tmp.cleanup()

    def switch(self, pid, model):
        prov = self.app.prefs.provider(pid)
        if model not in [m["id"] for m in prov["models"]]:
            prov["models"] = [m.to_json() for m in self.app.models(pid)] + [ModelInfo(model).to_json()]
        prov["model"] = model
        self.app._keep_effort_valid(pid)
        return self.app.prefs.provider(pid)["effort_level"], self.app.current_effort(pid, model)

    def test_switching_claude_models(self):
        self.app.prefs.provider("claude")["effort_level"] = "xhigh"
        self.assertEqual(self.switch("claude", "claude-opus-5-5"), ("xhigh", "xhigh"))
        self.assertEqual(self.switch("claude", "claude-haiku-4-5-20251001"), ("xhigh", None))  # no effort: untouched
        self.assertEqual(self.switch("claude", "claude-sonnet-5-5"), ("xhigh", "xhigh"))

    def test_switching_openai_models_snaps_and_saves(self):
        self.app.prefs.provider("openai")["effort_level"] = "max"
        self.assertEqual(self.switch("openai", "gpt-6.1-sol"), ("max", "max"))
        self.assertEqual(self.switch("openai", "gpt-5.4"), ("xhigh", "xhigh"))   # no max here
        self.assertEqual(self.switch("openai", "gpt-5.1"), ("high", "high"))     # no xhigh
        self.assertEqual(self.switch("openai", "gpt-5"), ("high", "high"))

    def test_fresh_prefs_start_at_high(self):
        self.assertEqual(self.app.current_effort("claude", "claude-opus-5-5"), "high")
        self.assertIsNone(self.app.current_effort("openai", "o3"))


if __name__ == "__main__":
    unittest.main()
