# Snapask

A small macOS helper: drag across part of your screen, and Claude's answer shows up
as a tiny one-line label in the bottom-left corner.

| Keys | What it does |
|------|--------------|
| **Ctrl+A** | Toggle screenshot mode (a small black dot follows your cursor) |
| **Drag** | Select a region. It's sent to Claude and the answer appears bottom-left |
| **Ctrl+S** | Cancel a pending request (nothing is shown), or hide the answer |
| **Click the answer** | Copy the full answer to the clipboard |
| **Ctrl+C** (in the terminal) | Quit |

## Setup

Requires macOS and Python 3.9+.

```bash
git clone https://github.com/<your-username>/snapask.git
cd snapask
python3 -m venv venv
source venv/bin/activate
pip install -r req.txt
cp .env.example .env
```

Open `.env` and replace `sk-ant-your-key-here` with your Anthropic API key
create one at [console.anthropic.com](https://console.anthropic.com);

### macOS permissions

In **System Settings → Privacy & Security**, give the app you run this from
(Terminal, iTerm, VS Code, ...) both:

- **Accessibility**, to catch Ctrl+A / Ctrl+S
- **Screen Recording**, to capture the selected region

Restart that app after granting them.

## Run

```bash
source venv/bin/activate
python snapask.py
```

## Settings

Near the top of `snapask.py`:

- `MODEL`: `claude-opus-5-5` (default), `claude-sonnet-5`, or `claude-haiku-4-5-20251001`
- `EFFORT`: `low` / `medium` / `high` / `xhigh` / `max`. Higher is smarter but slower and pricier
- `ANSWER_SECONDS`: auto-hide the answer after N seconds (0 = keep it until Ctrl+A / Ctrl+S)
- `SWALLOW_HOTKEYS`: set to `False` to let Ctrl+A / Ctrl+S also reach the app in front
- dot size and position, font size, and max label width