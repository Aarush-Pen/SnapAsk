"""Check snapask/effort_capabilities.py against the live API.

For every image-capable model your key lists, sends a tiny text-only request
at each level the table allows (expect success, using exactly the parameters
the app sends) and at one level the table leaves out (expect a 400).

    .venv/bin/python tools/check_effort_live.py claude
    .venv/bin/python tools/check_effort_live.py openai

The key comes from ANTHROPIC_API_KEY / OPENAI_API_KEY if set, otherwise from
SnapAsk's Keychain item (macOS may ask once to allow access). It is never
printed. Each request costs a fraction of a cent; the total is printed.
"""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402
import openai  # noqa: E402

from snapask import effort_capabilities as caps  # noqa: E402
from snapask.providers import PROVIDERS, Usage  # noqa: E402

PROMPT = "Reply with the single word OK."
ENV = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
CANDIDATES = {"claude": ("low", "medium", "high", "xhigh", "max"),
              "openai": ("none", "minimal", "low", "medium", "high", "xhigh", "max")}


def get_key(pid):
    if os.environ.get(ENV[pid]):
        return os.environ[ENV[pid]]
    out = subprocess.run(["security", "find-generic-password", "-s", f"SnapAsk.{pid}", "-w"],
                         capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"No {pid} key: set {ENV[pid]} or save one in SnapAsk first.")
    return out.stdout.strip()


def excluded_level(pid, spec):
    """One level the table leaves out, nearest the top of the model's range."""
    others = [lv for lv in CANDIDATES[pid] if lv not in spec.levels]
    if not spec.levels:
        return "low"
    above = [lv for lv in others if caps.ORDER.index(lv) > caps.ORDER.index(spec.levels[-1])]
    below = [lv for lv in others if caps.ORDER.index(lv) < caps.ORDER.index(spec.levels[0])]
    return (above or below or others or [None])[0]


def call(pid, key, info, level, use_app_params):
    """Returns (ok, detail, usage)."""
    prov = PROVIDERS[pid]
    try:
        if pid == "claude":
            if use_app_params:
                params = prov.request_settings(info, level)
            else:
                params = {"output_config": {"effort": level}}
                if info.meta.get("adaptive", True):
                    params["thinking"] = {"type": "adaptive"}
            params = {k: v for k, v in params.items() if k not in ("betas", "fallbacks")}
            params["max_tokens"] = 2048
            msg = anthropic.Anthropic(api_key=key, max_retries=2).messages.create(
                model=info.id, messages=[{"role": "user", "content": PROMPT}], **params)
            u = msg.usage
            usage = Usage(model=msg.model, input_tokens=u.input_tokens, output_tokens=u.output_tokens)
            return True, "", usage
        params = prov.request_settings(info, level) if use_app_params else {
            "store": False, "reasoning": {"effort": level}}
        resp = openai.OpenAI(api_key=key, max_retries=2).responses.create(
            model=info.id, input=PROMPT, **params)
        u = resp.usage
        cached = u.input_tokens_details.cached_tokens or 0
        usage = Usage(model=resp.model, input_tokens=u.input_tokens - cached, cached_tokens=cached,
                      output_tokens=u.output_tokens)
        return True, "", usage
    except (anthropic.BadRequestError, openai.BadRequestError) as e:
        return False, f"400: {' '.join(str(e.message).split())[:90]}", None
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:90]}", None


def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else "claude"
    key = get_key(pid)
    prov = PROVIDERS[pid]
    models = prov.list_models(key)
    jobs = []
    for info in models:
        spec = prov.effort_spec(info.id, models)
        if spec is None:
            jobs.append((info, None, "unlisted"))
            continue
        for level in spec.levels:
            jobs.append((info, level, "allowed"))
        bad = excluded_level(pid, spec)
        if bad:
            jobs.append((info, bad, "excluded"))
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(
            lambda j: (j, call(pid, key, j[0], j[1], j[2] != "excluded")), jobs))

    total, mismatches, rows = 0.0, [], {}
    for (info, level, kind), (ok, detail, usage) in results:
        if usage is not None:
            total += prov.cost_from_usage(usage) or 0
        row = rows.setdefault(info.id, {"ok": [], "rejected": [], "unexpected": [], "note": ""})
        if kind == "unlisted":
            row["note"] = "not in table: no effort sent (request ok)" if ok else f"not in table; {detail}"
        elif kind == "allowed":
            (row["ok"] if ok else row["unexpected"]).append(level if ok else f"{level} ({detail})")
        else:
            if ok:
                row["unexpected"].append(f"{level} accepted but table excludes it")
            else:
                row["rejected"].append(level)
        if row["unexpected"] and info.id not in mismatches:
            mismatches.append(info.id)
        api = info.meta.get("api_effort")
        spec = prov.effort_spec(info.id, models)
        if api is not None and spec is not None and tuple(api) != spec.levels:
            row["note"] = f"/v1/models says {api}, table says {list(spec.levels)}"
            if info.id not in mismatches:
                mismatches.append(info.id)

    print(f"\n{'model':30} {'accepted (table levels)':42} {'excluded → 400':16} notes")
    for model, r in rows.items():
        print(f"{model:30} {', '.join(r['ok']) or '-':42} {', '.join(r['rejected']) or '-':16} "
              f"{'; '.join(r['unexpected']) or r['note']}")
    print(f"\n{len(jobs)} requests, about ${total:.4f}. Mismatches: {', '.join(mismatches) or 'none'}")


if __name__ == "__main__":
    main()
