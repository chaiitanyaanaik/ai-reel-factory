# Product: ReelKut

Internal codebase name: **AI Reel Factory**. Product brand: **ReelKut**.

## Problem

Creators already have talking-head footage. They need a vertical Reel with B-roll, captions, and polish — **without rewriting dialogue** (lip sync must stay intact) and **without burning unbounded budget on AI video**.

## Product invariants

1. **Spoken audio is source of truth.** The cleaned “script” is a punctuated transcript, not a rewrite.
2. **B-roll is overlay, not concat** — original speech timeline (and lips) stay continuous.
3. **Video-first is default.** Teleprompter (topic → invent script → record) is secondary.
4. **Veo is capped** (`VEO_MAX_CLIPS`) and audio-off on the Developer API (`VEO_GENERATE_AUDIO=0`).
5. **No timeline editor (for now).** Polish is automatic via **style recipes** (`talking_head` default = legacy pacing/zoom; `tutorial` / `story` adjust gaps, A-roll share, and zoom density). Kill switch: `POLISH_RECIPES=0`.

## Modes

| Mode | Flow |
|------|------|
| `video_first` (default) | Upload clips → merge → enhance → transcribe → **plan from transcript** → align → broll → subtitles → render → cover |
| `teleprompter` | Topic → Gemini script/plan → (user records) → merge → enhance → … |

## Lip-sync guard

`script_engine/lip_sync.py` requires cleaned script word overlap ≥ 85% vs Whisper transcript. Failures abort the `plan` stage (unless `ALLOW_PLACEHOLDERS=1` for offline tests).

## Editor agent (B-roll pacing)

`script_engine/editor_agent.py` plans cutaways with craft rules + **code guardrails** (max count, duration, gaps, hook/ending on face, A-roll share). LLM proposes → validate → repair → `final_script.json`.

## Multi-user (Phase 1)

- Projects carry `owner_id`; list/get/jobs scoped to the signed-in user
- `AUTH_MODE=dev` — email login → JWT (local)
- `AUTH_MODE=clerk` — verify Clerk session JWT (public)
- Daily per-user caps on create project / start job / B-roll edits
- Landing + studio UI in `frontend/` (ReelKut brand)

## Cost controls

- Prefer Lite Veo + `VEO_MAX_CLIPS`
- Per-clip skip → keep A-roll
- `run_report.json` records sources per index
- Optional Langfuse traces (`LANGFUSE_*`)

## Demo script

1. Open landing → sign in → create project → upload clips  
2. Extract script — show `raw_script.md` matches speech  
3. Generate B-roll — show `source_report.json` / Veo prompts  
4. Final reel — overlay timing (`tpad`), safe-zone 9:16  

## CLI

```bash
python pipeline.py my_reel --mode video_first
python pipeline.py my_reel --mode teleprompter --from script --to script
```
