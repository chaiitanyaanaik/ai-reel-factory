# Pipeline analysis — will it work?

## Jupyter

**You don’t need Jupyter.** This project uses only Python scripts (`pipeline.py`, `video_engine/*.py`, etc.). There are no notebooks. You can ignore Jupyter.

---

## What should work

1. **Run from any directory**  
   The pipeline adds the project root to `sys.path`, so you can run:
   - `python pipeline.py project_001` from `ai_reel_factory`
   - Or `python "c:\Work documents\ai_reel_factory\pipeline.py" project_001` from anywhere

2. **`.env`**  
   With `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) in `.env`, Veo is used for B-roll. No need to set the variable in the shell.

3. **Stages in order**  
   Script → merge → transcribe → filler → align → broll → subtitles → render. Each stage only runs if its inputs exist (or it fails with a clear error).

4. **Missing B-roll**  
   If a B-roll clip is missing at render time (e.g. Veo was skipped), that segment falls back to the merged video with audio muted, so the pipeline still finishes.

---

## Requirements (must have)

| Requirement | Why |
|-------------|-----|
| **Python 3.10+** | Used type hints / syntax. |
| **FFmpeg on PATH** | Merge and render use it. Install (e.g. winget install FFmpeg) and ensure `ffmpeg` runs in a terminal. |
| **Clips in `raw_clips/`** | Merge needs at least one file (`01.mp4`, `02.mp4`, etc.). Empty folder → merge fails. |
| **`topic.txt`** | Script stage reads it. Missing → script stage fails. |

---

## Possible issues and fixes

### 1. Merge: “No clips in raw_clips”

- **Cause:** `projects/project_001/raw_clips/` is empty or has no `.mp4`/`.mov`.
- **Fix:** Add at least one video, e.g. `01.mp4`.

### 2. Transcribe: Whisper slow or “out of memory”

- **Cause:** First run downloads the model (~140 MB for `base`). Large videos or small RAM can make it slow or OOM.
- **Fix:** Use a smaller model by editing `video_engine/transcribe.py`: e.g. `model_size="tiny"` or `"small"` instead of `"base"`.

### 3. Veo: “Set GOOGLE_API_KEY” or 403 / 429

- **Cause:** No key in `.env`, or key invalid / quota exceeded.
- **Fix:** Put the key in `.env` (not in `.env.example`). If you hit rate limits, the pipeline will skip B-roll and use placeholders; you can re-run the broll stage later.

### 4. Veo: `operation.operations.get(operation)` or download fails

- **Cause:** SDK or API change, or network/proxy.
- **Fix:** Ensure `google-genai` is up to date (`pip install -U google-genai`). If you use a proxy, set `HTTPS_PROXY` (see google-genai docs).

### 5. Align: “actual_total” or empty timeline

- **Cause:** Transcript has no segments (e.g. silent or very short video).
- **Fix:** Already handled: we use the plan’s `total_duration_seconds` when there are no segments. If the plan has no beats, add a default in `final_script.json` or the script stage.

### 6. Render: FFmpeg “Invalid argument” or subtitle error

- **Cause:** Paths with spaces or special characters on Windows; or missing/corrupt SRT.
- **Fix:** Prefer a project path without spaces if you can. If subtitles fail, the pipeline still writes `reel.mp4` without burned-in subs when SRT is missing.

### 7. Script / editing plan are placeholders

- **Cause:** Script and editing plan are not wired to an LLM yet; they return fixed placeholders.
- **Effect:** Pipeline runs, but the reel is cut by the placeholder plan (e.g. 0–5 s A-roll, 5–8 s B-roll, rest extended). To get real scripts and plans, plug your LLM into `script_engine/generate_script.py` and `script_engine/editing_plan.py`.

---

## Quick sanity check

From the project root:

```bash
# 1. Dependencies
pip install -r requirements.txt

# 2. FFmpeg
ffmpeg -version

# 3. Project has topic and at least one clip
# - projects/project_001/topic.txt exists
# - projects/project_001/raw_clips/01.mp4 (or similar) exists

# 4. Run (full pipeline)
python pipeline.py project_001
```

If merge, transcribe, and render run without errors, the core path works. B-roll will be placeholder until Veo succeeds or you add clips under `broll/`.
