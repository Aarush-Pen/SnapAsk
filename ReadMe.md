# SnapAsk

A small macOS menu bar app: drag across part of your screen and a one-line answer
from **Claude**, **ChatGPT** or **Gemini** shows up in the bottom-left corner.

## Download

**[⬇ Download SnapAsk.dmg](https://github.com/Aarush-Pen/SnapAsk/releases/latest/download/SnapAsk.dmg)**
(macOS 13+, Apple silicon). All versions are on the [Releases page](https://github.com/Aarush-Pen/SnapAsk/releases).
You'll need your own API key from Anthropic, OpenAI or Google.

## Quick start

1. **Install.** Open `SnapAsk.dmg` ([download](#download)) and drag SnapAsk into Applications (or build it
   first with `./build.sh`, see [Build the app](#build-the-app)). Open it from Applications.
   If macOS says it can't verify SnapAsk, click **Done**, then
   **System Settings › Privacy & Security › Open Anyway** (once).
2. **Find it.** SnapAsk lives in the menu bar (viewfinder icon), not the Dock.
   Left-click the icon for settings, right-click for a menu with **Quit**.
3. **Grant permissions.** Turn SnapAsk on under **Accessibility** and **Screen Recording**
   in System Settings › Privacy & Security (the first-launch prompt has buttons for both),
   then quit and reopen SnapAsk.
4. **Add a key.** Pick Claude, ChatGPT or Gemini at the top of the window, paste your API
   key and click **Save**.
5. **Ask.** Press **⌃A**, drag over a question, and the answer appears bottom-left.
   Click it to copy.

**Permissions switched on but SnapAsk still asks for them?** That happens after a rebuild
(each ad-hoc signed build is a new app to macOS). Quit SnapAsk, run

```bash
tccutil reset Accessibility com.snapask.app
tccutil reset ScreenCapture com.snapask.app
```

then open SnapAsk and grant both again. To share SnapAsk with friends, send them
`dist/SnapAsk.dmg`; see [Share with friends](#share-with-friends).

## Keys

| Keys | What it does |
|------|--------------|
| **⌃A** | Toggle screenshot mode: the cursor becomes a crosshair, nothing else on screen changes |
| **Drag** | Press where the region starts (a small black dot marks it), release where it ends. It's sent to the active provider and the answer appears bottom-left |
| **⎋** or a click | Cancel the selection (nothing is captured or sent) |
| **⌃S** | Cancel a pending request (nothing is shown), or hide the answer |
| **⌃⌥,** | Show / hide the settings window |
| **⌃⌥P** | Switch to the next provider |
| **Click the answer** | Copy the full answer to the clipboard |

Every shortcut can be changed in the settings window: click it, then press the new keys
(⎋ cancels). A shortcut needs ⌃ or ⌥ (or ⌘ plus another modifier).

## Build the app

Requires macOS 13+ on Apple Silicon and Python 3.10+ (a [python.org](https://www.python.org/downloads/macos/) build is best).

```bash
./build.sh
```

That creates a fresh `.build-venv`, installs `requirements.txt` plus PyInstaller, and
writes an ad-hoc signed **`dist/SnapAsk.app`** (about 55 MB, no Python needed to run it)
and **`dist/SnapAsk.dmg`** to share (see below).
Move the app to `/Applications` and open it. It lives in the menu bar (no Dock icon):

- **Left-click** the menu bar icon to show or hide the settings window.
- **Right-click** (or ⌃-click) it for a menu: switch provider, take a screenshot, settings, Quit (⌘Q).

Opening SnapAsk again while it's running just brings up the settings window.

### Run from source instead

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py          # Ctrl+C in the terminal quits
```

When run from source, macOS permissions belong to the terminal app you start it from.

## Share with friends

Send them **`dist/SnapAsk.dmg`** (about 30 MB). It holds the app, an Applications
shortcut to drag it onto, and *How to open SnapAsk.txt* with these steps:

1. Drag SnapAsk into Applications and open it from there.
2. macOS says it can't verify SnapAsk, because it isn't signed by an Apple developer
   account. Click **Done**, then **System Settings › Privacy & Security › Open Anyway**.
   Only needed once.
3. Turn SnapAsk on under **Accessibility** and **Screen Recording**, then reopen it.
4. Paste their **own** API key. Keys stay in each person's Keychain.

It runs on Macs with Apple silicon (M1 or newer) and macOS 13+. Each friend's
permissions stick for good, until you send them a newer build: with ad-hoc signing
a new build counts as a new app, so they'd grant the two permissions again.

### Without the "Open Anyway" step

With a paid [Apple Developer account](https://developer.apple.com/programs/) ($99/year),
sign with your Developer ID and have Apple notarize it. Friends then just double-click,
and permissions survive updates (yours too, across rebuilds):

```bash
xcrun notarytool store-credentials snapask --apple-id you@example.com --team-id TEAMID   # once
SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" NOTARY_PROFILE=snapask ./build.sh
```

The hardened runtime settings it needs are in `assets/entitlements.plist`.

## Permissions

SnapAsk needs two permissions, under **System Settings › Privacy & Security**:

- **Accessibility**, so the keyboard shortcuts work in every app.
- **Screen Recording**, to capture the area you select.

On first launch SnapAsk explains this once and has buttons that open the right pane.
Turn **SnapAsk** on in each list. If capture still doesn't work, quit and reopen SnapAsk
(macOS applies Screen Recording on relaunch). Until both are granted, the settings
window shows a warning with a shortcut to the right pane, and the menu bar menu's
**Take Screenshot** still works without the hotkey.

**After rebuilding**, macOS treats the new ad-hoc signed build as a different app, and the
old grant silently stops applying even though SnapAsk still looks switched on. Reset
it, then grant again:

```bash
tccutil reset Accessibility com.snapask.app
tccutil reset ScreenCapture com.snapask.app
```

macOS may also ask once whether SnapAsk can read its saved keys; choose **Always Allow**.
(A Developer ID build, above, doesn't have this problem.)

## API keys

Paste a key in the settings window and click **Save**. SnapAsk checks it with a free
call (listing models) and only stores it if the provider accepts it.

- Keys live in the **macOS Keychain**, one item per provider: `SnapAsk.claude`,
  `SnapAsk.openai`, `SnapAsk.gemini` (search "SnapAsk" in Keychain Access).
- They are never written to preferences, logs or the app bundle. `.env` is no longer
  read; once your key is saved in the app you can delete `.env`.
- **Delete key** removes only that provider's key, and clears its tracked balance.

Get keys at [platform.claude.com](https://platform.claude.com/settings/keys),
[platform.openai.com](https://platform.openai.com/api-keys) and
[aistudio.google.com](https://aistudio.google.com/apikey).

## Providers, models and effort

The segmented control at the top picks the provider used for the next screenshot (so do
⌃⌥P and the menu bar menu). Everything below it shows that provider's key, balance,
model and effort.

**Model** lists the image-capable models your key can use, loaded live when you save a
key and on launch (with a built-in default list until then). The choice is remembered
per provider. Defaults: Claude Opus 5.5, GPT-6.1 Sol, Gemini 4 Argon.

**Effort** lists exactly the levels the selected model accepts, in the provider's own
terms, and the API's default is marked "(default)":

| | Sent as | Level names |
|---|---|---|
| Claude | `output_config.effort` (+ adaptive thinking where the model has it) | low, medium, high, xhigh ("Extra high"), max |
| ChatGPT | `reasoning.effort` (Responses API) | none, minimal, low, medium, high, xhigh, max |
| Gemini | `thinking_level` (Gemini 3+) or `thinking_budget` (2.5) | minimal, low, medium, high; Off on 2.5 Flash |

For example Claude Opus 4.6 has no Extra high, gpt-5.4 has None but no Max, and gpt-4.1
(not a reasoning model) shows "Not supported by this model". A model missing from the
table (say, one released last week) shows "Effort not configured for this model" and
no effort parameter is sent.

Your chosen level is remembered per provider. Switching to a model that doesn't offer it
picks the closest level it does (the next lower one if possible) and saves that.

The table lives in **`snapask/effort_capabilities.py`**, with the date it was checked and
the doc links. After editing it, `tools/check_effort_live.py claude` (or `openai`) sends a
tiny request per model and level to confirm the API agrees, and
`python -m unittest discover -s tests` runs the unit tests.

Every request's settings (never the key or the image) are logged, e.g.
`request provider=claude model=claude-opus-5-5 effort=high settings={'thinking': {'type': 'adaptive'}, 'output_config': {'effort': 'high'}, …}`.

## Balances

None of the three providers has an endpoint that returns your remaining prepaid credit
for a normal API key (Anthropic's and OpenAI's cost reports need admin keys and report
spending, not balance). So SnapAsk tracks it locally:

1. Look up your current balance on the provider's billing page (the **Billing ↗** link).
2. Type it into **Starting balance** and click **Set**.
3. After each answer SnapAsk reads the token usage (input, cached input, cache writes,
   output and reasoning/thinking tokens), prices it with `snapask/pricing.py`, and
   subtracts it. The caption shows the last request's cost and the total since you set it.

It's labelled "Estimated · tracked locally" because it can drift: a request you cancel
mid-way is still billed by the provider but isn't counted, and prices change. Re-enter
your real balance now and then, or click **Reset** to start over. Balances are saved in
the preferences file.

### Updating prices

Edit the tables in **`snapask/pricing.py`** (USD per million tokens; sources and the
date they were checked are at the top). Model ids match by longest prefix, so
`claude-haiku-4-5` also covers `claude-haiku-4-5-20251001`. A model missing from the
table still works, but its requests aren't counted (the window says so). Scheduled price
changes can be written with `changes_on=` (Gemini already has some). Rebuild afterwards.

## Files

| What | Where |
|---|---|
| Preferences (provider, models, effort, shortcuts, balances) | `~/Library/Application Support/SnapAsk/prefs.json` |
| Log (requests, usage, errors) | `~/Library/Logs/SnapAsk/snapask.log` |
| API keys | macOS Keychain |

## Project layout

```
main.py                  run from source
snapask/
  app.py                 app controller: wiring, state, quit
  capture.py             screenshot via `screencapture`
  overlay.py             screenshot mode (dot, drag) and the answer bubble
  hotkeys.py             global shortcuts (Quartz event tap) + recorder
  permissions.py         Accessibility / Screen Recording checks
  storage.py             Keychain keys + JSON preferences
  pricing.py             per-model prices, cost from token usage
  effort_capabilities.py effort levels each model accepts
  providers/             base.py (interface) + claude.py, chatgpt.py, gemini.py
  ui/                    settings_window.py, status_item.py, widgets.py
tools/make_icon.py       draws assets/SnapAsk.icns
tools/check_effort_live.py  checks the effort table against the live API
tests/                   unit tests (python -m unittest discover -s tests)
SnapAsk.spec, build.sh   packaging (app + shareable .dmg)
assets/                  icon, entitlements, friends' install steps
```

**Universal2:** the build targets Apple Silicon (`arm64`). A universal2 build is
possible with a python.org Python, but pydantic-core, jiter, cffi and cryptography
install arm64-only wheels, so their x86_64 wheels would have to be fused in (e.g. with
`delocate-fuse`) before switching `target_arch` in `SnapAsk.spec` to `universal2`.
