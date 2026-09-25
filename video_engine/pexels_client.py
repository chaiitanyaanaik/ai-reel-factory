"""
Pexels API client for free stock video B-roll.
Searches by keywords extracted from broll suggestions, downloads best vertical match.
Set PEXELS_API_KEY in .env or environment.
"""
from pathlib import Path
import os
import re
import requests

ROOT = Path(__file__).resolve().parent.parent

SEARCH_URL = "https://api.pexels.com/videos/search"
MIN_DURATION = 4
MIN_HEIGHT = 1080


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def get_pexels_key() -> str | None:
    _load_dotenv()
    return os.environ.get("PEXELS_API_KEY")


def extract_keywords(suggestion: str) -> str:
    """Turn a Subject/Camera/Scene suggestion into clean Pexels search terms.

    "A child's small hands reaching toward the camera. Camera: gentle dolly-in.
     Warm golden light shifting across the scene."
    -> "child hands reaching"
    """
    text = suggestion.strip()
    # Take only the subject part (before "Camera:")
    if "Camera:" in text or "camera:" in text:
        text = re.split(r"[Cc]amera:", text)[0]
    # Remove common filler phrases
    noise = [
        r"\bclose-up of\b", r"\ba\b", r"\ban\b", r"\bthe\b",
        r"\bslowly\b", r"\bgently\b", r"\bsoftly\b",
        r"\bwith\b", r"\band\b", r"\binto\b", r"\bfrom\b",
        r"\bthrough\b", r"\bacross\b", r"\btoward\b",
        r"\btheir\b", r"\bits\b", r"\bthat\b", r"\bwhich\b",
    ]
    for pat in noise:
        text = re.sub(pat, " ", text, flags=re.IGNORECASE)
    # Remove punctuation and extra spaces
    text = re.sub(r"[^a-zA-Z0-9 ]", " ", text)
    words = text.split()
    # Keep max 4 meaningful words for a focused search
    keywords = [w for w in words if len(w) > 2][:4]
    return " ".join(keywords)


def search_videos(query: str, orientation: str = "portrait",
                  per_page: int = 10) -> list[dict]:
    """Search Pexels for videos. Returns list of video result dicts."""
    key = get_pexels_key()
    if not key:
        return []
    resp = requests.get(
        SEARCH_URL,
        headers={"Authorization": key},
        params={
            "query": query,
            "orientation": orientation,
            "per_page": per_page,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        return []
    data = resp.json()
    return data.get("videos", [])


def _pick_best_file(video: dict) -> dict | None:
    """Pick the best video file: prefer HD vertical, at least MIN_DURATION seconds."""
    files = video.get("video_files", [])
    # Sort by height descending, prefer files with height >= MIN_HEIGHT
    candidates = sorted(files, key=lambda f: f.get("height", 0), reverse=True)
    for f in candidates:
        h = f.get("height", 0)
        w = f.get("width", 0)
        if h >= MIN_HEIGHT and h > w:
            return f
    # Fallback: any file with decent height
    for f in candidates:
        if f.get("height", 0) >= 720:
            return f
    return candidates[0] if candidates else None


def download_video(suggestion: str, output_path: Path,
                   min_duration: int = MIN_DURATION) -> Path | None:
    """Search Pexels using suggestion keywords and download the best match.

    Returns the output_path on success, None if no suitable video found.
    """
    keywords = extract_keywords(suggestion)
    if not keywords:
        return None

    videos = search_videos(keywords)
    if not videos:
        # Retry with fewer keywords
        short_query = " ".join(keywords.split()[:2])
        videos = search_videos(short_query)
    if not videos:
        return None

    # Filter by minimum duration
    suitable = [v for v in videos if v.get("duration", 0) >= min_duration]
    if not suitable:
        suitable = videos

    best_video = suitable[0]
    best_file = _pick_best_file(best_video)
    if not best_file or not best_file.get("link"):
        return None

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    resp = requests.get(best_file["link"], stream=True, timeout=60)
    if resp.status_code != 200:
        return None

    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    print(f"  [Pexels] Downloaded: \"{keywords}\" -> {output_path.name}")
    return output_path


def download_filler(output_path: Path) -> Path | None:
    """Download a generic neutral B-roll clip for filler segments."""
    return download_video(
        "soft sunlight window room calm peaceful",
        output_path,
        min_duration=4,
    )
