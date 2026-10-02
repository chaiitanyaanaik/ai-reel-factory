import { useEffect, useRef, useState } from "react";
import { fetchMediaObjectUrl } from "./api";

type Props = {
  /** API path like `/projects/.../video` — fetched with auth into a blob URL */
  srcPath?: string | null;
  /** Already-local blob/object URL (e.g. pending file preview) */
  localSrc?: string | null;
  emptyText?: string;
  /** Remount/reload when this changes (e.g. after re-render) */
  cacheKey?: string | number;
};

/**
 * Phone mockup video with authenticated load, play/pause, and volume controls.
 * Used on upload, B-roll, and final export screens.
 */
export default function PhoneVideo({
  srcPath,
  localSrc,
  emptyText = "Nothing to preview yet",
  cacheKey = "",
}: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(0.85);

  useEffect(() => {
    let cancelled = false;
    let created: string | null = null;

    setPlaying(false);
    setLoadError("");

    if (localSrc) {
      setBlobUrl(localSrc);
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }

    if (!srcPath) {
      setBlobUrl(null);
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }

    setLoading(true);
    setBlobUrl(null);
    (async () => {
      try {
        const url = await fetchMediaObjectUrl(srcPath);
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        created = url;
        setBlobUrl(url);
      } catch (e) {
        if (!cancelled) {
          setLoadError(String((e as Error).message || e));
          setBlobUrl(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [srcPath, localSrc, cacheKey]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    v.muted = muted;
    v.volume = muted ? 0 : volume;
  }, [muted, volume, blobUrl]);

  async function togglePlayback() {
    const v = videoRef.current;
    if (!v) return;
    if (!v.paused) {
      v.pause();
      return;
    }
    v.muted = muted;
    v.volume = muted ? 0 : volume;
    try {
      await v.play();
    } catch {
      // Browser blocked unmuted play — mute and retry, keep UI in sync
      try {
        v.muted = true;
        setMuted(true);
        await v.play();
      } catch (err) {
        setLoadError(String((err as Error).message || "Playback failed"));
      }
    }
  }

  function toggleMute(e: React.MouseEvent) {
    e.stopPropagation();
    const next = !muted;
    setMuted(next);
    const v = videoRef.current;
    if (v) {
      v.muted = next;
      if (!next && volume === 0) {
        setVolume(0.85);
        v.volume = 0.85;
      }
    }
  }

  function onVolumeChange(e: React.ChangeEvent<HTMLInputElement>) {
    e.stopPropagation();
    const next = Number(e.target.value);
    setVolume(next);
    if (next > 0 && muted) setMuted(false);
    if (next === 0) setMuted(true);
    const v = videoRef.current;
    if (v) {
      v.volume = next;
      v.muted = next === 0;
    }
  }

  const showVideo = Boolean(blobUrl) && !loadError;

  return (
    <div className="phone">
      {showVideo ? (
        <>
          <video
            ref={videoRef}
            key={blobUrl || "empty"}
            src={blobUrl || undefined}
            playsInline
            loop
            preload="auto"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
            onClick={() => void togglePlayback()}
          />
          <button
            type="button"
            className={`phone-play-btn${playing ? " is-playing" : ""}`}
            aria-label={playing ? "Pause" : "Play"}
            onClick={(e) => {
              e.stopPropagation();
              void togglePlayback();
            }}
          >
            {playing ? "❚❚" : "▶"}
          </button>
          <div className="phone-controls" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="phone-mute-btn"
              aria-label={muted || volume === 0 ? "Unmute" : "Mute"}
              onClick={toggleMute}
            >
              {muted || volume === 0 ? "🔇" : "🔊"}
            </button>
            <input
              type="range"
              className="phone-volume"
              min={0}
              max={1}
              step={0.05}
              value={muted ? 0 : volume}
              aria-label="Volume"
              onChange={onVolumeChange}
            />
          </div>
        </>
      ) : (
        <div className="phone-empty">
          {loading ? "Loading preview…" : loadError ? "Preview unavailable" : emptyText}
        </div>
      )}
    </div>
  );
}
