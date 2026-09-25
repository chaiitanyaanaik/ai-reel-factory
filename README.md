# AI Reel Factory

Converts a **topic + recorded video clips** into a **fully edited Instagram Reel** automatically.

Gemini writes the script (using your hook patterns, writing styles, and viral angles as reference), plans the edit, Pexels provides free stock B-roll (with Veo AI fallback), and FFmpeg renders the final vertical video -- all locally orchestrated.

---

## What It Does

1. **Generates a script** from your topic using Gemini, guided by your reference materials (hook patterns, viral angles, writing styles)
2. **Creates an editing plan** -- always starts with speaker (A-roll hook), then alternates with B-roll
3. **Merges raw clips** in filename order
4. **Transcribes** using Whisper (word-level timestamps)
5. **Aligns timeline** to real speech length; each B-roll shows for **~5 seconds max** (then back to speaker)
6. **Sources B-roll** -- Pexels first, optional Veo; speaker audio is boosted slightly in the final mix; B-roll clips never add their own audio
7. **Renders** 1080x1920 with 1.2x speed, zoom cuts, fades, white flash hook
8. **Generates cover** from final reel frames + script context (AI-first)

*(Optional: set `USE_FILLER_BROLL=1`, run the `filler` stage manually, then `align` — to cover um/ah with B-roll + mute again.)*

---

## Quick Start

Step-by-step commands (PowerShell): see **[PYTHON_COMMANDS.md](PYTHON_COMMANDS.md)** for the full checklist.

```powershell
# 1. Install + FFmpeg on PATH (see PYTHON_COMMANDS.md)
pip install -r requirements.txt

# 2. Keys
copy .env.example .env
# Edit .env: GOOGLE_API_KEY (required), PEXELS_API_KEY (recommended)

# 3. New project
mkdir projects\my_reel
# Edit projects\my_reel\topic.txt

# 4. Script + plan
python pipeline.py my_reel --from script --to script

# 5. Record clips → projects\my_reel\raw_clips\01.mp4, 02.mp4, ...

# 6. Merge → render (video)
python pipeline.py my_reel --from merge --to render
# → projects/my_reel/final/reel.mp4

# 7. (Optional) Instagram cover image
python pipeline.py my_reel --from cover --to cover
# → projects/my_reel/final/cover.jpg
```

**Default** `python pipeline.py my_reel` (no flags) runs **all** stages through **`cover`** → both `reel.mp4` and `cover.jpg`. To get video only: `python pipeline.py my_reel --to render`, then optionally run step 7 for the cover.

---

## Pipeline Stages

```
topic.txt ──► Script (Gemini + references) ──► Editing Plan (Gemini + references)
                                                     │
raw_clips/ ──► Merge ──► Transcribe (Whisper) ──► Align Timeline ──► B-roll ──► Render ──► Cover
                                                                                                 │
                                                                                   final/reel.mp4 + final/cover.jpg
```

| Stage | What it does | Key output |
|-------|-------------|------------|
| `script` | Gemini generates script + editing plan from topic + references | `raw_script.md`, `final_script.json` |
| `merge` | FFmpeg concatenates raw clips in filename order | `merged/merged.mp4` |
| `transcribe` | Whisper transcription with word timestamps | `transcripts/transcript.json` |
| `align` | Scales plan to transcript; caps each B-roll ~5s; optional fillers if `USE_FILLER_BROLL=1` | `cuts/timeline.json` |
| `broll` | Pexels stock first, Veo fallback | `broll/001.mp4, 002.mp4` (and `filler.mp4` only if fillers enabled) |
| `subtitles` | Creates SRT captions (fillers stripped) | `subtitles/captions.srt` |
| `render` | Composites A-roll + B-roll with effects | `final/reel.mp4` |
| `cover` | Extracts A-roll frame candidates from `final/reel.mp4`, uses full script context, and generates an AI cover image | `final/cover.jpg`, `cover/prompt.txt`, `cover/metadata.json` |

### Instagram reel cover (`cover` stage)

The `cover` stage produces a **fully AI-generated** vertical cover image for use as an Instagram Reel thumbnail/cover art. Identity comes from your **A-roll** (extracted frames from `final/reel.mp4`); layout and editorial style can follow **optional global reference images** at the repo root.

**Prerequisites**

- `projects/<name>/final/reel.mp4` (run `render` first, or use `--from cover --to cover` on an already-rendered project).
- `projects/<name>/raw_script.md` (full script text drives hook extraction in prompts).
- `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) for Gemini image models.

**What it does**

1. Extracts several early-frame JPEGs from `final/reel.mp4` via FFmpeg and picks a best candidate.
2. Builds an editorial-style prompt (magazine layout, asymmetry, single hook, no subtext — see `video_engine/cover.py`).
3. Sends a **multimodal** request in this order: **`[1]` identity frame** → **`[2]`, `[3]` … style references** (if present) → **text prompt last**.
4. Primary model: `COVER_IMAGE_MODEL` (default `gemini-3-pro-image-preview`). On failure, tries `COVER_IMAGE_FALLBACK_MODEL` (default `gemini-3.1-flash-image-preview`). If **both** fail, the stage **raises an error** (no non-AI stitched fallback).

**Global style references (optional, all projects)**

Place one or more images at the **repository root** (not under `projects/`):

| File (examples) | Role |
|-----------------|------|
| `cover_reference_style.png` | Single style/layout reference |
| `cover_reference_style_01.png` or `cover_reference_style_01.png.png` | Style reference 1 |
| `cover_reference_style_02.png` or `cover_reference_style_02.png.png` | Style reference 2 |

Detected files are attached after the A-roll frame and listed in `projects/<name>/cover/metadata.json` under `global_style_references_used`.

**Outputs**

| Path | Purpose |
|------|---------|
| `projects/<name>/final/cover.jpg` | Final cover image |
| `projects/<name>/cover/prompt.txt` | Exact prompt sent to the model |
| `projects/<name>/cover/metadata.json` | Model used, fallback flag, paths, errors |
| `projects/<name>/cover/selected_frame.jpg` | Chosen A-roll frame |
| `projects/<name>/cover/frame_candidates/` | Candidate frames from the reel |

**Environment variables** for cover are listed in the table below (`COVER_IMAGE_MODEL`, `COVER_IMAGE_FALLBACK_MODEL`, `COVER_VISION_MODEL`).

Run specific stages:

```powershell
python pipeline.py my_reel --from broll --to render

# Generate cover only (for already-rendered reels)
python pipeline.py my_reel --from cover --to cover
```

---

## Project Structure

```
ai_reel_factory/
├── pipeline.py              # Main orchestrator
├── style_guide.json         # Global visual style defaults
├── .env                     # API keys (not committed)
├── prompts/                 # LLM prompt templates
│   ├── script_generation.txt
│   └── editing_plan.txt
├── references/              # Creative reference materials (injected into LLM prompts)
│   ├── hook-patterns.md
│   ├── viral-angles.md
│   ├── writing-styles.md
│   └── visual-patterns.md
├── script_engine/           # Script + editing plan (Gemini)
│   ├── generate_script.py
│   ├── editing_plan.py
│   └── llm.py              # Shared Gemini client + reference loader
├── video_engine/            # Video processing
│   ├── merge_clips.py
│   ├── transcribe.py
│   ├── filler_detection.py
│   ├── timeline.py
│   ├── broll.py             # Pexels-first, Veo-fallback orchestrator
│   ├── pexels_client.py     # Free stock video search + download
│   ├── veo_client.py        # Veo API + style guide injection
│   ├── subtitles.py
│   ├── render.py            # FFmpeg filter_complex renderer
│   └── cover.py             # AI reel cover (A-roll frame + optional style refs + Gemini image)
└── projects/
    └── project_001/
        ├── topic.txt
        ├── style_guide.json  # Optional per-project style override
        ├── raw_clips/
        ├── merged/
        ├── transcripts/
        ├── cuts/
        ├── broll/
        ├── subtitles/
        ├── cover/              # cover stage: prompt, metadata, frame candidates
        └── final/
```

---

## Style Guide

A `style_guide.json` controls the visual look of all AI-generated content. Place one at the project root (global default) or inside a project folder (per-project override).

```json
{
  "visual_tone": "warm, golden hour, cinematic",
  "camera_style": "smooth steady movements, 35mm lens feel, shallow depth of field",
  "mood": "intimate, hopeful, gentle energy",
  "color_palette": "warm tones, soft highlights, muted shadows, natural lighting",
  "avoid": "no harsh lighting, no text overlays, no logos, no stock-photo feel",
  "format": "vertical 9:16, must look like real video not a photograph, continuous fluid motion throughout",
  "filler_clip": "Soft sunlight streaming through a window. Camera: slow steady pan. Dust particles floating, curtains swaying gently. No people, no text."
}
```

The style guide is injected into:
- **Gemini** when generating scripts and editing plans
- **Veo** as a prompt suffix for every B-roll clip (when Veo fallback is used). Optional `broll_casting` text is appended to Veo prompts when set in `style_guide.json` (e.g. regional or casting hints).
- **Filler clips** use the `filler_clip` field as their Veo generation prompt

---

## Reference Materials

Files in `references/` are injected directly into Gemini prompts so the LLM follows your creative frameworks:

| File | Used by | Content |
|------|---------|---------|
| `hook-patterns.md` | Script generation | 5 hook formulas (negative urgency, curiosity gap, etc.) |
| `viral-angles.md` | Script generation | 4 viral angle frameworks (villain, counter-intuitive, etc.) |
| `writing-styles.md` | Script + editing plan | Punchy & Deep Dive styles + anti-slop rules |
| `visual-patterns.md` | Editing plan | B-roll ratio, editing pacing, zoom cuts, text overlay rules |

Edit these files to change how scripts and plans are generated.

---

## Render Effects

The final render applies these effects via FFmpeg:

- **1.2x playback speed** on A-roll (audio + video)
- **Alternating zoom cuts** (1.1x center crop on every other A-roll segment)
- **Fade transitions** (0.25s fade-in/out on each B-roll overlay)
- **White flash** at video start (0.08s fade from white)
- **Speaker gain** (~2.5 dB by default; set `SPEAKER_VOLUME_DB=0` to disable)
- **Audio cleanup (optional)**: denoise/EQ/compression via env vars (see below)
- **Video clarity (optional)**: higher-quality scaling + optional denoise + mild sharpen via env vars (see below)
- **Filler muting** (only if `USE_FILLER_BROLL=1` and filler segments exist in the timeline)

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_API_KEY` | Yes | Google AI Studio key (for Gemini text + Veo video) |
| `PEXELS_API_KEY` | Recommended | Free Pexels key for stock B-roll ([get one here](https://www.pexels.com/api/)) |
| `GEMINI_MODEL` | No | Text model (default: `gemini-2.5-flash`) |
| `VEO_MODEL` | No | Video model for fallback (default: `veo-3.0-generate-001`) |
| `BROLL_USE_VEO` | No | Set to `0` to disable Veo fallback (Pexels only, zero cost) |
| `BROLL_VEO_ONLY` | No | Set to `1` to skip Pexels and generate **all** B-roll with Veo (script-aligned AI clips only) |
| `VEO_KINETIC_IG` | No | Set to `1` to enable IG-style kinetic Veo prompts (fast, high-energy B-roll). Off by default for more neutral/cinematic prompts. |
| `VEO_EXPAND_PROMPT` | No | Set to `1` to have Gemini expand each B-roll idea into a detailed Veo prompt (extra Gemini call per Veo clip; combines well with `VEO_KINETIC_IG=1`) |
| `SPEAKER_VOLUME_DB` | No | dB boost on speaker audio after speed-up (default `2.5`; set `0` to disable) |
| `BURN_IN_CAPTIONS` | No | `1` to burn captions into the final video (recommended); `0` to skip |
| `CAPTION_MARGIN_V` | No | Caption vertical margin/padding in pixels (default `80`) |
| `CAPTION_FONT_SIZE` | No | Caption font size for burn-in in points/pixels-ish (default `54`) |
| `FLASH_ON_BROLL` | No | `1` to show short white flashes at B-roll start times |
| `FLASH_DURATION_FLASH` | No | Flash duration in seconds (default `0.06`) |
| `AUDIO_DENOISE` | No | `0` (off), `afftdn` (default denoise), or `arnndn` (RNNoise model). `1/true` maps to `afftdn`. |
| `ARNNDN_MODEL_PATH` | No | Path to RNNoise model file when `AUDIO_DENOISE=arnndn` (if missing, falls back to `afftdn`). |
| `AUDIO_HIGHPASS_HZ` | No | High-pass cutoff for rumble removal (default `100`). |
| `AUDIO_LOWPASS_HZ` | No | Low-pass cutoff to tame hiss (default `12000`). |
| `AUDIO_COMPRESS` | No | `1` to enable gentle speech compression/limiting (default `1`). |
| `AUDIO_LOUDNORM` | No | `1` to apply loudness normalization (default `0`). |
| `VIDEO_DENOISE` | No | `1` to apply light `hqdn3d` denoise after scaling (default `0`). |
| `VIDEO_SHARPEN` | No | `1` to apply mild `unsharp` after scaling (default `1`). |
| `SCALE_FLAGS` | No | FFmpeg scale algorithm (default `lanczos`). |
| `MAX_BROLL_SECONDS` | No | Max seconds each B-roll stays on screen (default `5`) |
| `USE_FILLER_BROLL` | No | Set to `1` to insert um/ah filler overlays (run `run_filler_stage` from `pipeline.py` first, then re-run `align`) |
| `VEO_LOG_PROMPTS` | No | Set to `1` to **print** each Veo prompt and append to `projects/<name>/broll/veo_prompts.log` (timeline suggestion + full prompt) |
| `VEO_PROMPT_LOG` | No | Custom log file path when `VEO_LOG_PROMPTS=1` (default: `broll/veo_prompts.log`) |
| `RENDER_DEBUG_FILTERS` | No | `1` to print FFmpeg `filter_complex` for debugging/tuning. |
| `COVER_VISION_MODEL` | No | Gemini text model used for optional headline refinement in `build_cover_prompt_context` (default: `GEMINI_MODEL` or `gemini-2.5-flash`). |
| `COVER_IMAGE_MODEL` | No | Primary Gemini image model: identity + style refs + prompt (default: `gemini-3-pro-image-preview`). |
| `COVER_IMAGE_FALLBACK_MODEL` | No | Secondary Gemini image model if primary returns no image (default: `gemini-3.1-flash-image-preview`). |
| `COVER_REQUIRE_AI` | No | Reserved for metadata; cover output is **always** from an image model or the stage fails. |

Notes:
- When you run `pipeline.py`, the project reads keys and flags from `.env` (if `python-dotenv` is installed).
- **Existing** B-roll files are reused. If you switch to Veo-only and want fresh outputs, delete `projects/<name>/broll/*.mp4` first.
- **Cover:** Output is AI-only. If both `COVER_IMAGE_MODEL` and `COVER_IMAGE_FALLBACK_MODEL` fail, the stage errors; see `cover/metadata.json` for `error_message`.

## B-roll Sourcing Priority

1. **Existing clip on disk** -- if `broll/001.mp4` already exists, it's reused
2. **Pexels stock video** (free) -- keywords are auto-extracted from broll suggestions
3. **Veo AI generation** (paid) -- when Pexels has no match (or when `BROLL_VEO_ONLY=1`, which skips Pexels and uses Veo for every clip)

---

## Requirements

- **Python 3.11+**
- **FFmpeg** on PATH
- **Google API key** with Gemini access (GCP billing only needed if using Veo)
- **Pexels API key** (free, recommended) for stock B-roll
- See `requirements.txt` for Python packages
