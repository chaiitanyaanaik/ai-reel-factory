"""DeepFilterNet speech enhancement (optional dependency)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_DF_STATE: tuple[Any, Any, str] | None = None


def deepfilter_available() -> bool:
    try:
        import df.enhance  # noqa: F401
        import soundfile  # noqa: F401

        return True
    except ImportError:
        return False


def _init_df():
    global _DF_STATE
    if _DF_STATE is not None:
        return _DF_STATE
    from df.enhance import init_df

    from core.config import truthy

    model = os.environ.get("DEEPFILTERNET_MODEL", "DeepFilterNet3").strip() or "DeepFilterNet3"
    post_filter = truthy("DEEPFILTERNET_POST_FILTER", "0")
    _DF_STATE = init_df(
        model,
        post_filter=post_filter,
        log_level=os.environ.get("DEEPFILTERNET_LOG_LEVEL", "WARNING"),
        log_file=None,
    )
    return _DF_STATE


def denoise_wav(in_wav: Path, out_wav: Path) -> None:
    """Run DeepFilterNet on a PCM WAV; preserves original sample rate on output."""
    from df.enhance import enhance
    from df.io import load_audio, resample, save_audio
    from df.model import ModelParams

    model, df_state, _suffix = _init_df()
    df_sr = ModelParams().sr
    audio, meta = load_audio(str(in_wav), sr=df_sr, verbose=False)
    orig_sr = int(meta.sample_rate)
    if audio.dim() == 1:
        audio = audio.unsqueeze(0)
    enhanced = enhance(model, df_state, audio)
    enhanced = resample(enhanced.to("cpu"), df_sr, orig_sr)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    save_audio(str(out_wav), enhanced, sr=orig_sr, log=False)
