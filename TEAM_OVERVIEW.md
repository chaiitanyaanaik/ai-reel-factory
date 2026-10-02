# ReelKut — Team Overview

Video-first pipeline: upload talking-head clips → enhance → transcribe → lip-sync-safe edit plan → B-roll → vertical Reel + optional cover.

**Product UI:** Vite landing + studio (`frontend/`) talking to FastAPI (`api/`) with multi-user JWT auth.

## What it produces

- Cleaned transcript + edit plan: `raw_script.md`, `final_script.json` (same spoken words)
- Final video: `final/reel.mp4`
- Cover: `final/cover.jpg`
- Observability: `project.json`, `run_report.json`

## Default flow (video_first)

```mermaid
flowchart LR
  clips[raw_clips] --> merge[merge]
  merge --> enhance[enhance]
  enhance --> transcribe[transcribe]
  transcribe --> plan[plan lip-sync]
  plan --> align[align]
  align --> broll[broll]
  broll --> render[render]
  render --> cover[cover]
```

CLI: `python pipeline.py <name> --mode video_first`  
Dev (both): `npm install && npm run dev` from repo root → API `:8000`, UI http://127.0.0.1:5173  
Or split: `uvicorn api.main:app --port 8000` and `cd frontend && npm run dev`  

See [PRODUCT.md](PRODUCT.md) and [README.md](README.md).

## Where to look

| Area | Path |
|------|------|
| Orchestrator | `pipeline.py` |
| API + auth | `api/main.py`, `core/auth.py`, `core/project_store.py` |
| Jobs | `jobs/` |
| Contracts | `schemas/` |
| Plan from transcript | `script_engine/plan_from_transcript.py` |
| Lip-sync guard | `script_engine/lip_sync.py` |
| Editor pacing | `script_engine/editor_agent.py` |
| Veo B-roll | `video_engine/veo_client.py`, `video_engine/broll.py` |
| Per-user brand | `core/user_brand.py`, Studio **Brand**, `GET/PUT /auth/brand` |
| Enhance / render | `video_engine/enhance.py`, `video_engine/render.py` |
| Landing + studio | `frontend/src/Landing.tsx`, `App.tsx` |

## Brand vs craft prompts

- **Brand** is per user (`projects/.users/<id>/brand.json`): niche, audience, setting, casting, mood, avoid.
- Shared `prompts/`, `references/`, and `style_guide.json` are **craft-only** (editing rules, 9:16, literal B-roll). Niche examples live under `references/brand_templates/` and are not auto-loaded.
- Merge order for Gemini/Veo: global style guide → user brand → optional project `style_guide.json`.

## Config knobs

- Clerk auth: `AUTH_MODE=clerk` + host secrets in production; local `.env` from `.env.example`
- `BROLL_VEO_ONLY=1`, `VEO_MODEL=veo-3.1-lite-generate-preview`, `VEO_MAX_CLIPS=5`, `VEO_PARALLELISM=3`
- `AUDIO_PRESET=social`
- `RATE_LIMIT_*` for shared-key safety
- Optional `LANGFUSE_*` — categories `broll_generate` / `broll_regenerate` / `reel_rerender` with `user_id` + `project_id`
