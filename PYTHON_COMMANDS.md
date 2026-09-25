# Python commands — AI Reel Factory

Quick reference from the shell. Open PowerShell, `cd` to the repo root, then follow the steps below.

---

## Step-by-step: first-time setup (once per machine)

```powershell
cd "c:\Work documents\ai_reel_factory"
python -m pip install -r requirements.txt
python --version
```

1. **FFmpeg** must be on `PATH` (e.g. `winget install FFmpeg`), then open a new terminal.
2. **API keys:** copy `.env.example` to `.env` and set at least `GOOGLE_API_KEY`. Add `PEXELS_API_KEY` for free stock B-roll.

```powershell
copy .env.example .env
# Edit .env in your editor
```

---

## Step-by-step: new reel from scratch

Replace `my_reel` with your project folder name under `projects/`.

### 1. Create project and topic

```powershell
cd "c:\Work documents\ai_reel_factory"
mkdir projects\my_reel
# Create topic.txt (or echo one line — use a real editor for longer topics)
notepad projects\my_reel\topic.txt
```

### 2. Generate script + editing plan (Gemini)

```powershell
python pipeline.py my_reel --from script --to script
```

Outputs: `projects/my_reel/raw_script.md`, `projects/my_reel/final_script.json`

### 3. Record talking-head clips

Use `raw_script.md` as a teleprompter. Save clips as:

`projects/my_reel/raw_clips/01.mp4`, `02.mp4`, …

### 4. Merge through render (everything after recording)

```powershell
python pipeline.py my_reel --from merge --to render
```

This runs: merge → transcribe → align → broll → subtitles → render.

Outputs include: `projects/my_reel/final/reel.mp4`

### 5. (Optional) Generate Instagram reel cover image

Requires `final/reel.mp4` and `raw_script.md` from above.

Optional: put global style reference images at **repo root** (e.g. `cover_reference_style_01.png`, `cover_reference_style_02.png`).

```powershell
python pipeline.py my_reel --from cover --to cover
```

Outputs: `projects/my_reel/final/cover.jpg`, `projects/my_reel/cover/prompt.txt`, `projects/my_reel/cover/metadata.json`

---

## Step-by-step: one command for full pipeline (after topic + clips exist)

If `topic.txt` and `raw_clips/*.mp4` are already in place:

```powershell
cd "c:\Work documents\ai_reel_factory"
python pipeline.py my_reel
```

With **no** `--from` / `--to`, this runs **all** stages in order, **including `cover`** → `final/reel.mp4` and `final/cover.jpg`.

To **stop after the video** and skip cover (faster if you only need `reel.mp4`):

```powershell
python pipeline.py my_reel --to render
```

To run **only** from merge onward (script already done, clips in place):

```powershell
python pipeline.py my_reel --from merge --to cover
```

---

## Step-by-step: cover only (reel already rendered)

```powershell
cd "c:\Work documents\ai_reel_factory"
python pipeline.py my_reel --from cover --to cover
```

Needs: `projects/my_reel/final/reel.mp4` and `projects/my_reel/raw_script.md`.

Optional env overrides:

```powershell
$env:COVER_IMAGE_MODEL = "gemini-3-pro-image-preview"
$env:COVER_IMAGE_FALLBACK_MODEL = "gemini-3.1-flash-image-preview"
python pipeline.py my_reel --from cover --to cover
```

---

## Stages in order

`script` → `merge` → `transcribe` → `align` → `broll` → `subtitles` → `render` → `cover`

### Common partial runs

```powershell
# Script + plan only
python pipeline.py my_reel --from script --to script

# After clips exist: merge through end of pipeline (no cover)
python pipeline.py my_reel --from merge

# Re-run B-roll through render only
python pipeline.py my_reel --from broll --to render

# Re-render only (after timeline/subtitle tweaks)
python pipeline.py my_reel --from render --to render
```

Default project name if you omit it: `project_001`.

```powershell
python pipeline.py
```

---

## Environment variables (PowerShell examples)

```powershell
$env:GOOGLE_API_KEY = "your-key"
$env:PEXELS_API_KEY = "your-key"
$env:SPEAKER_VOLUME_DB = "2.5"
$env:MAX_BROLL_SECONDS = "5"
```

Or rely on `.env` (loaded when `python-dotenv` is installed).

---

## Optional: filler stage (um/ah B-roll)

Not in the default chain. After `transcribe`, if using `USE_FILLER_BROLL=1`:

```powershell
$env:USE_FILLER_BROLL = "1"
python -c "import sys; sys.path.insert(0, '.'); from pipeline import get_project_dir, run_filler_stage; run_filler_stage(get_project_dir('my_reel'))"
python pipeline.py my_reel --from align --to render
```

---

## Check Python

```powershell
python --version
python -c "import sys; print(sys.executable)"
```

---

## Run as a module (equivalent to `python pipeline.py`)

```powershell
python -m pipeline my_reel --from merge
```

---

*For pipeline architecture, cover stage details, and env var tables, see [README.md](README.md).*
