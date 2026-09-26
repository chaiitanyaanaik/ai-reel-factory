"""
Langfuse observability helper.

When LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY are set, emits traces with
user_id / project_id / job_id. Otherwise all helpers are no-ops.
"""
from __future__ import annotations

import contextvars
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from core.config import load_env

logger = logging.getLogger("ai_reel_factory.tracing")

_user_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "trace_user_id", default=None
)
_user_email: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "trace_user_email", default=None
)
_project_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "trace_project_id", default=None
)
_job_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "trace_job_id", default=None
)

_client: Any = None
_client_checked = False


# Rough USD estimates when the provider does not return cost
_GEMINI_INPUT_PER_M = 0.10
_GEMINI_OUTPUT_PER_M = 0.40
_VEO_PER_SECOND = 0.05


def set_trace_context(
    *,
    user_id: str | None = None,
    user_email: str | None = None,
    project_id: str | None = None,
    job_id: str | None = None,
) -> None:
    if user_id is not None:
        _user_id.set(user_id)
    if user_email is not None:
        _user_email.set(user_email)
    if project_id is not None:
        _project_id.set(project_id)
    if job_id is not None:
        _job_id.set(job_id)


def clear_trace_context() -> None:
    _user_id.set(None)
    _user_email.set(None)
    _project_id.set(None)
    _job_id.set(None)


def get_trace_context() -> dict[str, str | None]:
    return {
        "user_id": _user_id.get(),
        "user_email": _user_email.get(),
        "project_id": _project_id.get(),
        "job_id": _job_id.get(),
    }


def _enabled() -> bool:
    load_env()
    return bool(
        (os.environ.get("LANGFUSE_PUBLIC_KEY") or "").strip()
        and (os.environ.get("LANGFUSE_SECRET_KEY") or "").strip()
    )


def get_langfuse():
    """Lazy Langfuse client, or None if disabled / unavailable."""
    global _client, _client_checked
    if _client_checked:
        return _client
    _client_checked = True
    if not _enabled():
        return None
    try:
        from langfuse import Langfuse

        kwargs: dict[str, Any] = {
            "public_key": os.environ["LANGFUSE_PUBLIC_KEY"].strip(),
            "secret_key": os.environ["LANGFUSE_SECRET_KEY"].strip(),
        }
        host = (os.environ.get("LANGFUSE_HOST") or "").strip()
        if host:
            kwargs["host"] = host
        _client = Langfuse(**kwargs)
        return _client
    except Exception as e:
        logger.warning("Langfuse init failed (tracing disabled): %s", e)
        _client = None
        return None


def estimate_gemini_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens / 1_000_000 * _GEMINI_INPUT_PER_M
        + output_tokens / 1_000_000 * _GEMINI_OUTPUT_PER_M
    )


def estimate_veo_cost_usd(duration_seconds: int) -> float:
    return max(0, int(duration_seconds)) * _VEO_PER_SECOND


@dataclass
class Observation:
    """In-process span; flushes to Langfuse when available."""

    name: str
    as_type: str = "span"
    metadata: dict[str, Any] = field(default_factory=dict)
    input: Any = None
    output: Any = None
    model: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = None
    level: str = "DEFAULT"
    status_message: str | None = None
    _t0: float = field(default_factory=time.monotonic)
    _obs: Any = None

    def update(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
        cost_usd: float | None = None,
        model: str | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        if output is not None:
            self.output = output
        if metadata:
            self.metadata.update(metadata)
        if usage:
            self.usage.update(usage)
        if cost_usd is not None:
            self.cost_usd = cost_usd
        if model is not None:
            self.model = model
        if level is not None:
            self.level = level
        if status_message is not None:
            self.status_message = status_message
        if self._obs is not None:
            try:
                kwargs: dict[str, Any] = {}
                if output is not None:
                    kwargs["output"] = _truncate(output)
                if metadata:
                    kwargs["metadata"] = {**self.metadata}
                if usage:
                    kwargs["usage_details"] = dict(self.usage)
                if model is not None:
                    kwargs["model"] = model
                if level is not None:
                    kwargs["level"] = level
                if status_message is not None:
                    kwargs["status_message"] = status_message
                if cost_usd is not None:
                    kwargs.setdefault("metadata", self.metadata)
                    self.metadata["cost_usd"] = cost_usd
                    kwargs["metadata"] = dict(self.metadata)
                if kwargs:
                    self._obs.update(**kwargs)
            except Exception:
                pass

    def end(self, *, error: Exception | None = None, close_sdk: bool = True) -> float:
        duration_ms = (time.monotonic() - self._t0) * 1000
        self.metadata["duration_ms"] = round(duration_ms, 1)
        if error is not None:
            self.level = "ERROR"
            self.status_message = str(error)
            self.metadata["error"] = str(error)
        if self._obs is not None:
            try:
                self._obs.update(
                    output=_truncate(self.output) if self.output is not None else None,
                    metadata=dict(self.metadata),
                    level=self.level,
                    status_message=self.status_message,
                    **(
                        {"usage_details": dict(self.usage)}
                        if self.usage and self.as_type == "generation"
                        else {}
                    ),
                    **({"model": self.model} if self.model else {}),
                )
                if close_sdk:
                    end = getattr(self._obs, "end", None)
                    if callable(end):
                        end()
            except Exception:
                pass
        return duration_ms


def _truncate(value: Any, limit: int = 4000) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 1] + "…"
    if isinstance(value, dict):
        return {k: _truncate(v, limit) for k, v in value.items()}
    return value


def _attach_trace_identity(obs: Any) -> None:
    """Set user/session on the current trace when the SDK supports it."""
    ctx = get_trace_context()
    try:
        update_trace = getattr(obs, "update_trace", None)
        if callable(update_trace):
            update_trace(
                user_id=ctx.get("user_id"),
                session_id=ctx.get("project_id") or ctx.get("job_id"),
                metadata={
                    k: v
                    for k, v in {
                        "project_id": ctx.get("project_id"),
                        "job_id": ctx.get("job_id"),
                        "user_email": ctx.get("user_email"),
                    }.items()
                    if v
                },
            )
    except Exception:
        pass


@contextmanager
def observe(
    name: str,
    *,
    as_type: str = "span",
    input: Any = None,
    metadata: dict[str, Any] | None = None,
    model: str | None = None,
) -> Iterator[Observation]:
    """Context manager span/generation. No-op when Langfuse is off."""
    meta = {
        **{k: v for k, v in get_trace_context().items() if v},
        **(metadata or {}),
    }
    obs = Observation(
        name=name,
        as_type=as_type,
        metadata=meta,
        input=input,
        model=model,
    )
    client = get_langfuse()
    if client is not None:
        try:
            start = getattr(client, "start_as_current_observation", None)
            if callable(start):
                cm = start(
                    as_type=as_type,
                    name=name,
                    input=_truncate(input) if input is not None else None,
                    metadata=meta,
                    **({"model": model} if model and as_type == "generation" else {}),
                )
                inner = cm.__enter__()
                obs._obs = inner
                _attach_trace_identity(inner)
                try:
                    yield obs
                    obs.end(close_sdk=False)
                    cm.__exit__(None, None, None)
                except Exception as e:
                    obs.end(error=e, close_sdk=False)
                    cm.__exit__(type(e), e, e.__traceback__)
                    raise
                return
            # Legacy SDK v2-style
            if hasattr(client, "trace"):
                trace = client.trace(
                    name=name,
                    user_id=meta.get("user_id"),
                    session_id=meta.get("project_id") or meta.get("job_id"),
                    metadata=meta,
                )
                if as_type == "generation":
                    child = trace.generation(
                        name=name,
                        model=model,
                        input=_truncate(input) if input is not None else None,
                        metadata=meta,
                    )
                else:
                    child = trace.span(
                        name=name,
                        input=_truncate(input) if input is not None else None,
                        metadata=meta,
                    )
                obs._obs = child
                try:
                    yield obs
                    obs.end()
                except Exception as e:
                    obs.end(error=e)
                    raise
                return
        except Exception as e:
            logger.debug("Langfuse observe fallback to local: %s", e)

    try:
        yield obs
        obs.end()
    except Exception as e:
        obs.end(error=e)
        raise


def flush() -> None:
    client = get_langfuse()
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        pass
