# SnapAsk

A small macOS helper: drag across part of your screen, and Claude's answer shows up
as a tiny one-line label in the bottom-left corner.

| Keys | What it does |
|------|--------------|
| **Ctrl+A** | Toggle screenshot mode (a small black dot follows your cursor) |
| **Drag** | Press at the region's top-left corner (the dot jumps up to the cursor tip and stays there to mark it), release at the bottom-right. No box is drawn. The region is sent to Claude and the answer appears bottom-left |
| **Ctrl+S** | Cancel a pending request (nothing is shown), or hide the answer |
| **Click the answer** | Copy the full answer to the clipboard |
| **Ctrl+C** (in the terminal) | Quit |

## Requirements

- A Mac
- Python **3.10 or newer** (developed on 3.13)
- An Anthropic API key with credits. Every screenshot you send is one paid API request

## Install

### 1. Get a recent Python

```bash
python3 --version
```

If that prints 3.10 or higher, skip ahead. If it prints **3.9.x** (the version Apple
ships) or "command not found", install the latest Python from
[python.org/downloads](https://www.python.org/downloads/macos/). Then open a **new**
Terminal window and check again.

### 2. Download SnapAsk

```bash
cd ~
git clone https://github.com/Aarush-Pen/SnapAsk.git
cd SnapAsk
```

This puts it in `~/SnapAsk`, which the rest of this guide assumes. If `git` isn't
installed, macOS will offer to install the Command Line Tools: accept, then run the
command again. If the repo is private, sign in to GitHub first, for example with
`gh auth login`.

### 3. Create the virtual environment and install packages

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r req.txt
```

Everything is installed inside the project's `.venv/` folder, and nothing is installed
system-wide. To check that it worked:

```bash
.venv/bin/python -c "import anthropic, AppKit, Quartz, dotenv; print('ok')"
```

### 4. Add your API key

Create a key at [console.anthropic.com](https://console.anthropic.com), then:

```bash
cp -n .env.example .env
open -e .env
```

In the TextEdit window that opens, replace `sk-ant-your-key-here` with your key, then
save and close. `.env` is git-ignored, so your key is never committed, and `git pull`
never touches it. The `-n` means an existing `.env` is never overwritten, so running
this step again keeps the key you already saved.

### 5. Grant macOS permissions

The permissions go to **the app you run SnapAsk from** (Terminal, iTerm, VS Code, ...),
not to SnapAsk itself. In **System Settings → Privacy & Security**, turn that app on
under both:

- **Accessibility**, so SnapAsk can catch Ctrl+A / Ctrl+S
- **Screen Recording** (called **Screen & System Audio Recording** on newer macOS),
  so it can capture the region you select

If your app isn't in the list, add it with the **+** button. Then **quit that app
completely (⌘Q) and reopen it**. The permissions don't take effect until it restarts.

### 6. Run it

```bash
cd ~/SnapAsk
.venv/bin/python snapask.py
```

You should see `snapask running.` SnapAsk only works while this terminal window stays
open (minimizing it is fine). To quit, press Ctrl+C in that window.

To try it out:

1. Press Ctrl+A. A small dot appears just below your cursor.
2. Press with the cursor tip at the top-left of a question (the dot jumps up to
   the tip to mark it) and drag to its bottom-right.
3. The answer appears in the bottom-left corner. Click it to copy.

### Optional: a `snapask` command

To start SnapAsk from any terminal by typing `snapask`:

```bash
echo "alias snapask='$HOME/SnapAsk/.venv/bin/python $HOME/SnapAsk/snapask.py'" >> ~/.zshrc
source ~/.zshrc
```

## Updating a copy you already cloned

If you set SnapAsk up with `git clone` earlier, this brings it up to date. Your `.env`
(API key) and macOS permissions carry over, so you don't need to redo steps 4 and 5.

1. Quit SnapAsk if it's running (Ctrl+C in its terminal window).
2. Get the latest code:

   ```bash
   cd ~/SnapAsk        # or wherever you cloned it
   git pull
   ```

3. Install any updated packages:

   ```bash
   .venv/bin/python -m pip install -r req.txt
   ```

4. Start it again with `.venv/bin/python snapask.py` (or `snapask` if you made the
   alias).

**If your copy has a `venv/` folder instead of `.venv/`**, it was set up with an
older version of this guide. The same applies if step 3 fails with
`No such file or directory` or `No matching distribution found`. In either case,
replace the environment with a fresh one. Make sure `python3 --version` shows 3.10
or newer first (see Install step 1):

```bash
rm -rf venv .venv
python3 -m venv .venv
.venv/bin/python -m pip install -r req.txt
```

**If `git pull` says "divergent branches"**, or your folder has a `main.py` and a
`snapask/` folder, you cloned SnapAsk 2.0, the old menu bar app. It can't be updated
in place, so set the folder aside and do a fresh install:

```bash
mv ~/SnapAsk ~/SnapAsk-2.0-old
```

Then follow [Install](#install) from step 2. Once the new version works, you can
delete `~/SnapAsk-2.0-old`. SnapAsk 2.0 is still available as the `v2.0.0` release on
GitHub.

**If `git pull` says your local changes "would be overwritten"**, you edited a file
that the update also changes, usually the settings in `snapask.py`. You have two
options:

- Keep your edits and reapply them on top of the update:

  ```bash
  git stash
  git pull
  git stash pop
  ```

  If `git stash pop` reports a conflict, open the file it names and pick the lines
  you want between the `<<<<<<<` and `>>>>>>>` markers.
- Throw your edits away and take the update as-is. This permanently discards your
  changes to tracked files, but not your `.env`:

  ```bash
  git restore .
  git pull
  ```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `pip install` fails with "No matching distribution found" | The venv was made with a Python older than 3.10. Do step 1, then `rm -rf .venv` and repeat step 3 |
| `No API key found` | `.env` is missing, or the key wasn't pasted in. It must start with `sk-ant-` (step 4) |
| `Couldn't listen for Ctrl+A / Ctrl+S` | Your terminal app needs Accessibility, and must be restarted after you grant it (step 5) |
| The answer is `⚠` | The request failed. The error is printed in the terminal. Common causes: an invalid key, no credits, or no internet |
| The answer is `?` or has nothing to do with what you selected | Usually Screen Recording isn't granted to your terminal app (step 5). Otherwise, select a larger, clearer area |
| Ctrl+A / Ctrl+S stop working in other apps | This is expected while SnapAsk runs, because it takes those keys. Set `SWALLOW_HOTKEYS = False` to share them |

## Settings

Near the top of `snapask.py`:

- `MODEL`: `claude-opus-5-5` (default), `claude-sonnet-5-5`, or `claude-haiku-5-5`
- `EFFORT`: `low` / `medium` / `high` / `xhigh` / `max`. Higher is smarter but slower and pricier
- `ANSWER_SECONDS`: auto-hide the answer after N seconds (0 = keep it until Ctrl+A / Ctrl+S)
- `SWALLOW_HOTKEYS`: set to `False` to let Ctrl+A / Ctrl+S also reach the app in front
- dot size and position, font size, and max label width

## Uninstall

1. Quit SnapAsk with Ctrl+C.
2. Delete the folder: `rm -rf ~/SnapAsk`.
3. If you added the `snapask` alias, remove that line from `~/.zshrc`.
4. Optionally, turn your terminal app back off under Accessibility and Screen Recording.
