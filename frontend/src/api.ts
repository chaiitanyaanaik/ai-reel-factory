/** API client — Vite proxy in dev; Bearer auth for multi-user. */

const TOKEN_KEY = "reelkut_token";
const USER_KEY = "reelkut_user";
const MEDIA_TOKEN_KEY = "reelkut_media_token";
const MEDIA_TOKEN_EXP_KEY = "reelkut_media_token_exp";

export type AuthUser = {
  id: string;
  email: string;
  name?: string | null;
  is_admin?: boolean;
};

export type AdminUserRow = {
  user_id?: string | null;
  email?: string | null;
  name?: string | null;
  last_seen_at?: string | null;
  pending?: boolean;
  plan: "free" | "paid";
  project_limit?: number | null;
  unlimited: boolean;
  projects_used: number;
  source?: string;
  entitlement?: {
    plan?: string;
    project_limit?: number | null;
    notes?: string;
  } | null;
};

export type BrandProfile = {
  niche?: string | null;
  audience?: string | null;
  visual_tone?: string | null;
  mood?: string | null;
  color_palette?: string | null;
  camera_style?: string | null;
  setting?: string | null;
  visual_world?: string | null;
  broll_casting?: string | null;
  avoid?: string | null;
  filler_clip?: string | null;
  format?: string | null;
};

export type UsageSnapshot = {
  day: string;
  limits: Record<string, number>;
  used: Record<string, number>;
  projects: {
    used: number;
    limit: number | null;
    unlimited: boolean;
    plan: string;
  };
  is_admin?: boolean;
};

type TokenProvider = () => Promise<string | null>;

let tokenProvider: TokenProvider | null = null;
let memoryToken: string | null = null;

/** Clerk (or other) async session token source. */
export function setTokenProvider(provider: TokenProvider | null) {
  tokenProvider = provider;
}

/** Keep a sync copy for media URLs while Clerk getToken() is async. */
export function setMemoryToken(token: string | null) {
  memoryToken = token;
  if (token) {
    try {
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* ignore */
    }
  }
}

export type ProjectSummary = {
  id: string;
  name?: string | null;
  mode: string;
  status: string;
  topic?: string | null;
  owner_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  clip_count: number;
  has_reel: boolean;
  has_script: boolean;
  has_cover: boolean;
  artifacts: Record<string, string>;
};

export type ClipInfo = {
  index: number;
  filename: string;
  size_bytes: number;
  duration_seconds?: number | null;
  url: string;
};

export type ProjectDetail = {
  manifest: {
    id: string;
    name?: string | null;
    status: string;
    mode: string;
    topic?: string | null;
    owner_id?: string | null;
    current_stage?: string | null;
    error?: string | null;
  };
  artifacts: Record<string, string>;
  clips: ClipInfo[];
  run_report: {
    status: string;
    stages?: { name: string; status: string; error?: string | null }[];
    errors?: string[];
    veo_clips_used?: number;
    broll?: { source: string; detail?: string | null }[];
  } | null;
  artifact_urls: Record<string, string>;
  error?: string | null;
};

export function getStoredToken(): string | null {
  if (memoryToken) return memoryToken;
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getStoredUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch {
    return null;
  }
}

export function setSession(token: string, user: AuthUser) {
  memoryToken = token;
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  memoryToken = null;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem(MEDIA_TOKEN_KEY);
  localStorage.removeItem(MEDIA_TOKEN_EXP_KEY);
}

/** True for stale email-login JWTs — not Clerk RS256, and not short-lived media tokens. */
export function isLegacyReelkutToken(token: string | null | undefined): boolean {
  if (!token) return false;
  try {
    const parts = token.split(".");
    if (parts.length < 2) return false;
    const payloadJson = atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"));
    const payload = JSON.parse(payloadJson) as { iss?: string; typ?: string };
    if (payload.typ === "media" || payload.iss === "reelkut-media") return false;
    return payload.iss === "reelkut-dev";
  } catch {
    return false;
  }
}

/**
 * Drop stale email-login tokens so they are never sent as Bearer under Clerk.
 * (Those JWTs have no `kid` → API error: Unable to find a signing key that matches "None".)
 */
export function purgeLegacyAuthArtifacts() {
  const stored = getStoredToken();
  if (isLegacyReelkutToken(stored)) {
    clearSession();
  }
  if (isLegacyReelkutToken(memoryToken)) {
    memoryToken = null;
  }
}

async function resolveToken(): Promise<string | null> {
  if (tokenProvider) {
    try {
      const t = await tokenProvider();
      if (t) {
        // Never keep a legacy HS256 token once Clerk is providing sessions.
        if (!isLegacyReelkutToken(t)) {
          memoryToken = t;
          return t;
        }
      }
    } catch {
      /* fall through */
    }
  }
  const stored = getStoredToken();
  if (isLegacyReelkutToken(stored)) {
    clearSession();
    return null;
  }
  return stored;
}

function authHeaders(extra?: HeadersInit): HeadersInit {
  const token = getStoredToken();
  const base: Record<string, string> = {};
  if (token) base.Authorization = `Bearer ${token}`;
  return { ...base, ...(extra as Record<string, string>) };
}

/**
 * Prefer short-lived media token in query strings (not the session JWT).
 * Same-origin <video> may also authenticate via httponly cookie.
 */
export function withAccessToken(url: string): string {
  void ensureMediaToken();
  const token = getCachedMediaToken() || getStoredToken();
  if (!token) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}access_token=${encodeURIComponent(token)}`;
}

const MEDIA_TIMEOUT_MS = 45_000;

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error(`${label} timed out`)), ms);
    promise.then(
      (v) => {
        window.clearTimeout(timer);
        resolve(v);
      },
      (e) => {
        window.clearTimeout(timer);
        reject(e);
      }
    );
  });
}

/** Await a media token, then return a playable URL (for &lt;video src&gt;). */
export async function authenticatedUrl(path: string): Promise<string> {
  await withTimeout(ensureMediaToken(), MEDIA_TIMEOUT_MS, "Media token");
  const token = getCachedMediaToken() || (await withTimeout(resolveToken(), MEDIA_TIMEOUT_MS, "Auth"));
  if (!token) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}access_token=${encodeURIComponent(token)}`;
}

/**
 * Playable media URL for &lt;video&gt;/&lt;img&gt;.
 * - Images/posters: prefer short-lived ?access_token= (fast, no full download wait).
 * - Videos: always fetch with Authorization into a blob: URL — Safari/Clerk often
 *   fail to play &lt;video src="...?access_token="&gt; (Range requests / token quirks).
 * Caller must revokeObjectURL only when the returned URL starts with blob:.
 */
export async function fetchMediaObjectUrl(path: string): Promise<string> {
  const isVideo =
    /\/video(?:\?|$)/.test(path) ||
    path.includes("/artifacts/reel") ||
    /\.mp4(?:\?|$)/i.test(path);

  if (!isVideo) {
    try {
      const url = await authenticatedUrl(path);
      if (url.includes("access_token=")) return url;
    } catch {
      /* fall through to blob */
    }
  }

  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), MEDIA_TIMEOUT_MS);
  try {
    const res = await apiFetch(path, { signal: ctrl.signal });
    if (!res.ok) {
      throw new Error(await parseError(res));
    }
    const blob = await res.blob();
    if (!blob.size) {
      throw new Error("Empty media response");
    }
    const typed =
      blob.type && blob.type !== "application/octet-stream"
        ? blob
        : new Blob([blob], { type: isVideo ? "video/mp4" : blob.type || "application/octet-stream" });
    return URL.createObjectURL(typed);
  } catch (e) {
    if ((e as Error).name === "AbortError") {
      throw new Error("Media request timed out");
    }
    throw e;
  } finally {
    window.clearTimeout(timer);
  }
}

/** Download an authenticated artifact (works under Clerk). */
export async function downloadAuthenticated(path: string, filename: string): Promise<void> {
  const res = await apiFetch(path);
  if (!res.ok) throw new Error(await parseError(res));
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function getCachedMediaToken(): string | null {
  try {
    const token = localStorage.getItem(MEDIA_TOKEN_KEY);
    const exp = Number(localStorage.getItem(MEDIA_TOKEN_EXP_KEY) || 0);
    if (!token || !exp || Date.now() >= exp - 60_000) return null;
    return token;
  } catch {
    return null;
  }
}

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data.detail === "string") return data.detail;
    return JSON.stringify(data.detail ?? data);
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const token = await resolveToken();
  const headers: Record<string, string> = {
    ...(authHeaders(init?.headers) as Record<string, string>),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(input, { ...init, headers });
  if (res.status === 401) {
    clearSession();
  }
  return res;
}

async function ensureMediaToken(): Promise<string | null> {
  const cached = getCachedMediaToken();
  if (cached) return cached;
  if (!(await resolveToken())) return null;
  try {
    const res = await apiFetch("/auth/media-token");
    if (!res.ok) return null;
    const data = (await res.json()) as { access_token?: string; expires_in?: number };
    if (!data.access_token) return null;
    const ttlMs = Math.max(300, Number(data.expires_in || 3600)) * 1000;
    localStorage.setItem(MEDIA_TOKEN_KEY, data.access_token);
    localStorage.setItem(MEDIA_TOKEN_EXP_KEY, String(Date.now() + ttlMs));
    return data.access_token;
  } catch {
    return null;
  }
}

export async function login(email: string, name?: string): Promise<AuthUser> {
  const res = await fetch("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, name: name || undefined }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  const data = await res.json();
  setSession(data.access_token, data.user);
  await ensureMediaToken();
  return data.user as AuthUser;
}

export async function logout(): Promise<void> {
  try {
    await apiFetch("/auth/logout", { method: "POST" });
  } catch {
    /* ignore */
  }
  clearSession();
}

export async function fetchMe(): Promise<AuthUser | null> {
  const token = await resolveToken();
  if (!token) return null;
  const res = await apiFetch("/auth/me");
  if (!res.ok) {
    clearSession();
    return null;
  }
  const user = (await res.json()) as AuthUser;
  localStorage.setItem(USER_KEY, JSON.stringify(user));
  await ensureMediaToken();
  return user;
}

/** Push Clerk client email/name so admin checks work when the JWT omits email. */
export async function syncAuthProfile(email: string, name?: string | null): Promise<AuthUser> {
  const res = await apiFetch("/auth/sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, name: name || undefined }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  const user = (await res.json()) as AuthUser;
  localStorage.setItem(USER_KEY, JSON.stringify(user));
  return user;
}

export async function fetchUsage(): Promise<UsageSnapshot> {
  const res = await apiFetch("/auth/usage");
  if (!res.ok) throw new Error(await parseError(res));
  return (await res.json()) as UsageSnapshot;
}

export async function getBrand(): Promise<BrandProfile> {
  const res = await apiFetch("/auth/brand");
  if (!res.ok) throw new Error(await parseError(res));
  return (await res.json()) as BrandProfile;
}

export async function saveBrand(brand: BrandProfile): Promise<BrandProfile> {
  const res = await apiFetch("/auth/brand", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(brand),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return (await res.json()) as BrandProfile;
}

export async function listAdminUsers(): Promise<AdminUserRow[]> {
  const res = await apiFetch("/admin/users");
  if (!res.ok) throw new Error(await parseError(res));
  const data = await res.json();
  return (data.users ?? []) as AdminUserRow[];
}

export async function patchAdminUser(
  userId: string,
  body: { plan: string; project_limit?: number | null; notes?: string }
): Promise<void> {
  const res = await apiFetch(`/admin/users/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await parseError(res));
}

export async function upsertAdminUserByEmail(body: {
  email: string;
  plan: string;
  project_limit?: number | null;
  notes?: string;
}): Promise<void> {
  const res = await apiFetch("/admin/users/by-email", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await parseError(res));
}

export async function listProjects(): Promise<ProjectSummary[]> {
  const res = await apiFetch("/projects");
  if (!res.ok) throw new Error(await parseError(res));
  const data = await res.json();
  return data.projects ?? [];
}

export async function createProject(name?: string, topic?: string) {
  const res = await apiFetch("/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: name || undefined,
      topic: topic || undefined,
      mode: "video_first",
    }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function updateProject(
  id: string,
  body: { name?: string | null; topic?: string | null }
) {
  const res = await apiFetch(`/projects/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getProject(id: string): Promise<ProjectDetail> {
  const res = await apiFetch(`/projects/${id}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function uploadClips(id: string, files: File[], replace = false) {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  const q = replace ? "?replace=true" : "";
  const res = await apiFetch(`/projects/${id}/clips${q}`, { method: "POST", body: form });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function reorderClips(id: string, order: string[]) {
  const res = await apiFetch(`/projects/${id}/clips/order`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ order }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function deleteClip(id: string, filename: string) {
  // POST (not DELETE) — some local/prod proxies return 405 for DELETE.
  const res = await apiFetch(`/projects/${id}/clips/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<{
    project_id: string;
    deleted: string;
    clips: ClipInfo[];
    invalidated: boolean;
  }>;
}

export async function startJob(
  id: string,
  from_stage: string,
  to_stage: string
): Promise<{ job_id: string; status: string }> {
  const res = await apiFetch(`/projects/${id}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: "video_first",
      from_stage,
      to_stage,
    }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getJob(id: string, jobId: string) {
  const res = await apiFetch(`/projects/${id}/jobs/${jobId}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getScript(id: string): Promise<string> {
  const res = await apiFetch(`/projects/${id}/script`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.text();
}

export function artifactUrl(id: string, name: "reel" | "cover" | "script") {
  return withAccessToken(`/projects/${id}/artifacts/${name}`);
}

export function clipUrl(id: string, filename: string) {
  return withAccessToken(`/projects/${id}/clips/${filename}`);
}

export type BrollClip = {
  broll_index: number;
  suggestion?: string | null;
  spoken_text?: string | null;
  full_prompt?: string | null;
  source?: string | null;
  path?: string | null;
  detail?: string | null;
  video_url?: string | null;
  poster_url?: string | null;
  chat?: { role: string; content: string }[];
  versions?: unknown[];
};

export async function listBroll(projectId: string): Promise<BrollClip[]> {
  const res = await apiFetch(`/projects/${projectId}/broll`);
  if (!res.ok) throw new Error(await parseError(res));
  const data = await res.json();
  return (data.broll ?? []) as BrollClip[];
}

export async function editBroll(
  projectId: string,
  index: number,
  message: string
): Promise<BrollClip> {
  const res = await apiFetch(`/projects/${projectId}/broll/${index}/edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export function brollVideoUrl(projectId: string, index: number) {
  return withAccessToken(`/projects/${projectId}/broll/${index}/video`);
}

export async function pollJobUntilDone(
  projectId: string,
  jobId: string,
  onTick?: (status: string, stage?: string | null) => void
): Promise<void> {
  const started = Date.now();
  for (;;) {
    const detail = await getProject(projectId);
    const job = await getJob(projectId, jobId);
    const elapsedSec = Math.round((Date.now() - started) / 1000);
    const stage = detail.manifest.current_stage;
    onTick?.(
      `${job.status}${stage ? ` · ${stage}` : ""} · ${elapsedSec}s`,
      null
    );
    if (job.status === "completed") return;
    if (job.status === "failed") {
      throw new Error(job.error || detail.error || "Job failed");
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
}
