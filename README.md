# ReelKut (AI Reel Factory)

**ReelKut** turns uploaded talking-head clips into paced vertical Reels — lip-sync safe, with cutaways and captions. The spoken audio is never rewritten.

This repo is the local product + pipeline: **Vite landing & studio UI**, **FastAPI** job API with multi-user auth, and a **CLI** orchestrator.

| Doc | Purpose |
|-----|---------|
| [PRODUCT.md](PRODUCT.md) | Product invariants, API contract, demo script |
| [DESIGN.md](DESIGN.md) | Architecture (overlay, `tpad`, Veo prompts) |
| [TEAM_OVERVIEW.md](TEAM_OVERVIEW.md) | Short onboarding map |
| [frontend/COLOR.md](frontend/COLOR.md) | Landing / UI design tokens |

---

## What it does

1. **Merge** raw clips (`01.mp4` / `1.mov`, …)
2. **Enhance** speech (FFmpeg social preset)
3. **Transcribe** with Whisper (word-level timestamps)
4. **Plan** with an editor agent — cleaned transcript + A-roll/B-roll beats (**same spoken words**)
5. **Align** beats to real speech times; safety-cap each B-roll length
6. **Source B-roll** — reuse → **Veo** (default) / optional Pexels → else keep A-roll
7. **Subtitles** + **render** 1080×1920 (speed, zoom cuts, overlays)
8. **Cover** (optional) — AI thumbnail from reel frames + script

**Secondary mode (`teleprompter`):** topic → Gemini invents a script → you record → same video chain from merge onward.

---

## Product highlights (current)

- **Creator landing** (`frontend/`) — ReelKut brand, blue→indigo hero, Studio login, creator showcase
- **Multi-user Phase 1** — JWT auth (`AUTH_MODE=dev` locally; `clerk` for public), projects scoped by `owner_id`
- **Lip-sync guard** — plan stage rejects dialogue rewrites (≥85% word overlap)
- **Veo Lite B-roll** — spoken-line–aware prompts, audio-off on Developer API, capped clip count
- **Per-user rate limits** — daily caps on projects / jobs / B-roll edits (shared API key safety)

---

## Requirements

- Python **3.11+**
- **FFmpeg** on `PATH`
- **Node 20+** (frontend)
- `GOOGLE_API_KEY` (Gemini + Veo)
- `PEXELS_API_KEY` optional if you turn Veo-only off

```bash
cd /path/to/ai-reel-factory   # folder that contains pipeline.py
python3 -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # fill in keys
```

---

## Quick start (UI)

```bash
# Terminal 1 — API
uvicorn api.main:app --host 127.0.0.1 --port 8000

# Terminal 2 — UI
cd frontend && npm install && npm run dev
# → http://127.0.0.1:5173
```

1. Open the **landing** → sign in with email (dev auth)  
2. **Library** — create a project or open a past reel  
3. **Upload** — add clips, reorder with ↑↓, upload  
4. **Extract script** — merge → enhance → transcribe → editor plan  
5. **Generate B-roll** — align → Veo  
6. **Final reel** — subtitles → render (+ optional cover)  

Vite proxies `/auth`, `/projects`, and `/health` to the API (see `frontend/vite.config.ts`).

---

## Quick start (CLI, video-first)

```bash
mkdir -p projects/my_reel/raw_clips
# copy takes into raw_clips/ as 01.mp4, 02.mp4 (or 1.mov, 2.mov)

python pipeline.py my_reel --mode video_first --to render
# → projects/my_reel/final/reel.mp4

python pipeline.py my_reel --mode video_first --from cover --to cover
# → projects/my_reel/final/cover.jpg
```

Resume after a failure:

```bash
python pipeline.py my_reel --mode video_first --from plan --to render
```

### Teleprompter mode

```bash
mkdir -p projects/my_reel
echo "Your topic here" > projects/my_reel/topic.txt
python pipeline.py my_reel --mode teleprompter --from script --to script
# record using raw_script.md → drop clips into raw_clips/
python pipeline.py my_reel --mode teleprompter --from merge --to render
```

---

## Modes & stage order

```text
video_first (default):
  merge → enhance → transcribe → plan → align → broll → subtitles → render → cover

teleprompter:
  script → merge → enhance → transcribe → align → broll → subtitles → render → cover
```

| Flag | Meaning |
|------|---------|
| `--mode video_first\|teleprompter` | Pipeline mode |
| `--from STAGE` | Start at stage |
| `--to STAGE` | Stop after stage |

---

## Pipeline stages

| Stage | Role | Key outputs |
|-------|------|-------------|
| `merge` | FFmpeg concat of `raw_clips/` | `merged/merged.mp4` |
| `enhance` | Social audio preset | `merged/enhanced.mp4` |
| `transcribe` | Whisper (uses enhanced if present) | `transcripts/transcript.json` |
| `plan` | Editor agent: cleaned script + beats + guardrails | `raw_script.md`, `final_script.json` |
| `script` | Teleprompter only: invent script + plan from topic | same as above |
| `align` | Map beats to transcript; cap B-roll length | `cuts/timeline.json` |
| `broll` | Veo / optional Pexels / skip→A-roll | `broll/00N.mp4`, `source_report.json` |
| `subtitles` | SRT (filler words stripped from caption text) | `subtitles/captions.srt` |
| `render` | Overlay composite @ 1080×1920 | `final/reel.mp4` |
| `cover` | AI cover image | `final/cover.jpg` |

Each run also updates `project.json` and `run_report.json`.

---

## Product invariants

1. **Spoken audio is source of truth.** `raw_script.md` is a punctuated transcript, not a rewrite.
2. **Lip-sync guard** — cleaned script must overlap ≥85% of transcript words (`script_engine/lip_sync.py`).
3. **B-roll is overlay, not concat** — speaker audio (and lips) stay continuous. FFmpeg `tpad` keeps overlay timing correct.
4. **Editor pacing is enforced in code**, not prompt-only (`script_engine/editor_agent.py`).

---

## Auth (multi-user Phase 1)

| Mode | Env | Notes |
|------|-----|--------|
| `dev` (default) | `AUTH_MODE=dev` `AUTH_REQUIRED=1` | Email login → JWT; **local only** |
| `off` | `AUTH_MODE=off` | Legacy open local API |
| `clerk` | `AUTH_MODE=clerk` + `CLERK_ISSUER` | Verify Clerk Bearer JWT — **public rollout** |

Frontend optional Clerk: set `VITE_CLERK_PUBLISHABLE_KEY` in `frontend/.env` (see `frontend/.env.example`).

**Production checklist:**

1. `ENVIRONMENT=production` — blocks `AUTH_MODE=dev` / `off` and weak `AUTH_SECRET`
2. `AUTH_MODE=clerk` + Clerk on the frontend; never expose provider keys to the browser
3. Secrets via host env / secret manager (`.dockerignore` keeps `.env` out of images)
4. One **prod** Google key + one **staging** key; enable billing + budget alerts
5. Per-user caps: `RATE_LIMIT_PROJECTS_PER_DAY`, `RATE_LIMIT_JOBS_PER_DAY`, `RATE_LIMIT_BROLL_EDITS_PER_DAY`
6. `COOKIE_SECURE=1` on HTTPS; `CORS_ORIGINS` = real frontend only
7. Media via short-lived `/auth/media-token` when possible

`GET /auth/usage` returns the current user’s daily counters.

---

## API routes

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Liveness + auth mode |
| `POST` | `/auth/login` | Dev login → JWT |
| `GET` | `/auth/me` | Current user |
| `GET` | `/auth/media-token` | Short-lived token for media URLs |
| `GET` | `/auth/usage` | Per-user daily rate-limit counters |
| `POST` | `/auth/logout` | Clear cookie |
| `GET` | `/projects` | List **your** projects |
| `POST` | `/projects` | Create project |
| `GET` | `/projects/{id}` | Status + clips + artifacts |
| `POST` | `/projects/{id}/clips` | Upload (`?replace=true` clears first) |
| `GET` | `/projects/{id}/clips` | List clips in merge order |
| `PUT` | `/projects/{id}/clips/order` | Resequence |
| `GET` | `/projects/{id}/script` | Cleaned transcript text |
| `POST` | `/projects/{id}/jobs` | Start job |
| `GET` | `/projects/{id}/jobs/{job_id}` | Job status |
| `GET` | `/projects/{id}/artifacts/reel` | Download reel |

Interactive docs: http://127.0.0.1:8000/docs  

---

## Editor agent (B-roll planning)

| Guardrail | Default env |
|-----------|-------------|
| Max B-rolls per video | `EDITOR_MAX_BROLL_COUNT=5` |
| B-roll length | `EDITOR_MIN_BROLL_SECONDS=2` … `EDITOR_MAX_BROLL_SECONDS=5` |
| Min gap between B-rolls | `EDITOR_MIN_BROLL_GAP=3.5` |
| Face on hook | `EDITOR_MIN_HOOK_AROLL=3` |
| Face on ending | `EDITOR_MIN_END_AROLL=2.5` |
| Minimum A-roll share | `EDITOR_MIN_AROLL_SHARE=0.55` |

`final_script.json` may include `editor_validation`, `editor_repairs`, and `editor_rules`.

---

## B-roll sourcing

**Current product default: Google Veo Lite** (see `.env.example`).

| Setting | Typical value |
|---------|----------------|
| `BROLL_VEO_ONLY` | `1` |
| `BROLL_USE_VEO` | `1` |
| `VEO_MODEL` | `veo-3.1-lite-generate-preview` |
| `VEO_DURATION_SECONDS` | `4` |
| `VEO_RESOLUTION` | `720p` |
| `VEO_GENERATE_AUDIO` | `0` (Developer API; B-roll is muted in render) |
| `VEO_MAX_CLIPS` | `5` |

Prompts include the **spoken line** for the beat plus casting/style from `style_guide.json`. Optional: `VEO_EXPAND_PROMPT`, `VEO_LOG_PROMPTS`.

Priority per segment: reuse existing file → Veo → skip (keep A-roll). Set `BROLL_VEO_ONLY=0` and add `PEXELS_API_KEY` to prefer stock.

---

## Render effects

- **1.2×** speed (video + audio)
- White flash at start
- Alternating zoom cuts on A-roll
- B-roll fullscreen overlays with fades (`tpad` + timed `overlay`)
- Speaker gain (`SPEAKER_VOLUME_DB`)
- Optional burn-in captions (`BURN_IN_CAPTIONS`)

---

## Project layout

```text
ai-reel-factory/
├── pipeline.py
├── api/main.py                 # FastAPI (ReelKut API)
├── jobs/  schemas/  core/      # jobs, contracts, auth, project store
├── script_engine/              # plan, editor agent, lip_sync, llm
├── video_engine/               # merge, enhance, veo, render, …
├── frontend/                   # Vite + React (landing + studio)
│   ├── public/landing/         # Stitch showcase images
│   └── src/Landing.tsx App.tsx …
├── prompts/  references/  style_guide.json
├── tests/  Dockerfile  docker-compose.yml
└── projects/<id>/
    ├── project.json            # includes owner_id when authenticated
    ├── run_report.json
    ├── raw_script.md  final_script.json
    ├── raw_clips/  merged/  transcripts/  cuts/
    ├── broll/  subtitles/  cover/  final/
```

```bash
python -c "from core.project_store import create_project; print(create_project(name='my_reel').id)"
```

---

## Environment variables

Copy `.env.example` → `.env`. Highlights:

| Variable | Notes |
|----------|--------|
| `GOOGLE_API_KEY` | Required for plan / Veo / cover |
| `AUTH_MODE` / `AUTH_SECRET` | Multi-user auth |
| `BROLL_USE_VEO` / `VEO_*` | B-roll generation |
| `CORS_ORIGINS` | Vite origin(s) |
| `RATE_LIMIT_*` | Per-user daily caps |
| `AUDIO_PRESET` / `SPEAKER_VOLUME_DB` | Enhance + render |
| `LANGFUSE_*` | Optional cost/latency traces |

Full list: `.env.example`.

---

## Tests, CI, Docker

```bash
pytest tests/test_unit.py tests/test_editor_agent.py -q
pytest tests/test_integration_enhance.py -q   # needs ffmpeg

docker compose up --build                     # API on :8000, projects/ mounted
```

CI: `.github/workflows/ci.yml` runs unit + enhance integration tests on PR/push.

---

## Troubleshooting

| Symptom | What to do |
|---------|------------|
| `Project not found` | Create `projects/<name>/` or use `create_project` |
| `No clips in raw_clips` | Add `.mp4` / `.mov` files |
| Auth / Not Found on `/auth/*` | Run API on `:8000`; Vite proxy must include `/auth` |
| Gemini `503` | Re-run `--from plan`; client retries + model fallbacks |
| Veo `429` / quota | Wait / lower `VEO_MAX_CLIPS`; check AI Studio billing |
| Veo `generate_audio` errors | Keep `VEO_GENERATE_AUDIO=0` on Developer API |
| Empty B-roll | Check `broll/source_report.json` and `veo_prompts.log` |
| Cover fails | Needs `final/reel.mp4` + `raw_script.md` + image model access |

---

## License / notes

Local orchestration + product UI. API keys stay in `.env` (not committed). Cap Veo usage and use per-user rate limits when sharing keys across accounts.
