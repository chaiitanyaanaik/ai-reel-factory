import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth, useClerk, useUser, UserButton } from "@clerk/react";
import {
  clearSession,
  createProject,
  deleteClip,
  downloadAuthenticated,
  editBroll,
  fetchMe,
  fetchMediaObjectUrl,
  fetchUsage,
  getBrand,
  getProject,
  getScript,
  getStoredToken,
  getStoredUser,
  listBroll,
  listProjects,
  logout,
  pollJobUntilDone,
  purgeLegacyAuthArtifacts,
  reorderClips,
  saveBrand,
  setMemoryToken,
  setSession,
  setTokenProvider,
  startJob,
  syncAuthProfile,
  updateProject,
  uploadClips,
  type AuthUser,
  type BrandProfile,
  type BrollClip,
  type ClipInfo,
  type ProjectDetail,
  type ProjectSummary,
  type StyleRecipe,
  type UsageSnapshot,
} from "./api";

const STYLE_RECIPES: {
  id: StyleRecipe;
  label: string;
  blurb: string;
}[] = [
  {
    id: "talking_head",
    label: "Talking head",
    blurb: "Face-forward tips — light cutaways (default).",
  },
  {
    id: "tutorial",
    label: "Tutorial",
    blurb: "Teach/clarify — denser cutaways and zooms.",
  },
  {
    id: "story",
    label: "Story",
    blurb: "Narrative presence — fewer cutaways, more face.",
  },
];
import { isClerkConfigured } from "./clerkConfig";
import Admin from "./Admin";
import Landing from "./Landing";
import PhoneVideo, { PhoneStill } from "./PhoneVideo";

type View = "library" | "wizard" | "brand" | "admin";
type Step = "upload" | "script" | "broll" | "final";

const EMPTY_BRAND: BrandProfile = {
  niche: "",
  audience: "",
  setting: "",
  broll_casting: "",
  mood: "",
  visual_tone: "",
  color_palette: "",
  avoid: "",
  camera_style: "",
};

const BRAND_FILL_KEYS: (keyof BrandProfile)[] = [
  "niche",
  "audience",
  "setting",
  "visual_world",
  "broll_casting",
  "mood",
  "visual_tone",
  "color_palette",
  "avoid",
  "camera_style",
];

function brandIsFilled(brand: BrandProfile): boolean {
  return BRAND_FILL_KEYS.some((key) => String(brand[key] ?? "").trim().length > 0);
}

/** Library badge: prefer artifacts, then job status (failed → completed after a later success). */
function projectStatusBadge(p: {
  status: string;
  has_reel?: boolean;
  has_script?: boolean;
}): { label: string; kind: "completed" | "failed" | "running" | "script" | "pending" } {
  if (p.has_reel || p.status === "completed") {
    return { label: "Completed", kind: "completed" };
  }
  if (p.status === "failed") {
    return { label: "Retry", kind: "failed" };
  }
  if (p.status === "running") {
    return { label: "Running", kind: "running" };
  }
  if (p.has_script) {
    return { label: "Script", kind: "script" };
  }
  return { label: "Pending", kind: "pending" };
}

const STEPS: { id: Step; label: string; num: string }[] = [
  { id: "upload", label: "Upload & Sequence", num: "01" },
  { id: "script", label: "Extract Script", num: "02" },
  { id: "broll", label: "Generate B-Roll", num: "03" },
  { id: "final", label: "Final Export", num: "04" },
];

function formatBytes(n: number) {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Keep in sync with API defaults (MAX_*_DURATION_SECONDS). */
const MAX_CLIP_SECONDS = 180;
const MAX_PROJECT_SECONDS = 180;

/** Whole seconds for UI (e.g. 60 → "60s"). */
function formatSeconds(seconds: number) {
  return `${Math.max(0, Math.round(seconds))}s`;
}

function probeFileDuration(file: File): Promise<number> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    video.preload = "metadata";
    video.onloadedmetadata = () => {
      const d = video.duration;
      URL.revokeObjectURL(url);
      if (!Number.isFinite(d) || d <= 0) {
        reject(new Error(`Could not read duration for “${file.name}”.`));
        return;
      }
      resolve(d);
    };
    video.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error(`Could not read “${file.name}”. Use MP4 or MOV.`));
    };
    video.src = url;
  });
}

async function validateVideoFiles(
  files: File[],
  existingSeconds: number
): Promise<{ ok: { file: File; duration: number }[]; error?: string }> {
  let running = existingSeconds;
  const ok: { file: File; duration: number }[] = [];
  for (const file of files) {
    let duration: number;
    try {
      duration = await probeFileDuration(file);
    } catch (e) {
      return { ok: [], error: String((e as Error).message || e) };
    }
    if (duration > MAX_CLIP_SECONDS + 0.05) {
      return {
        ok: [],
        error: `Video length can't be more than ${MAX_CLIP_SECONDS} seconds.`,
      };
    }
    if (running + duration > MAX_PROJECT_SECONDS + 0.05) {
      return {
        ok: [],
        error: `Video length can't be more than ${MAX_PROJECT_SECONDS} seconds.`,
      };
    }
    running += duration;
    ok.push({ file, duration });
  }
  return { ok };
}

function ClerkSessionBridge({
  onReady,
  onSignedOut,
}: {
  onReady: (user: AuthUser) => void;
  onSignedOut: () => void;
}) {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const { user } = useUser();

  useEffect(() => {
    // Clear email-login JWTs left in localStorage from AUTH_MODE=dev sessions.
    purgeLegacyAuthArtifacts();
    setTokenProvider(async () => {
      if (!isSignedIn) return null;
      return (await getToken()) || null;
    });
    return () => setTokenProvider(null);
  }, [getToken, isSignedIn]);

  useEffect(() => {
    if (!isLoaded) return;
    let cancelled = false;
    (async () => {
      if (!isSignedIn || !user) {
        setMemoryToken(null);
        clearSession();
        onSignedOut();
        return;
      }
      try {
        purgeLegacyAuthArtifacts();
        const token = await getToken();
        if (cancelled || !token) return;
        const authUser: AuthUser = {
          id: user.id,
          email: user.primaryEmailAddress?.emailAddress || "",
          name: user.fullName || user.firstName || null,
        };
        setSession(token, authUser);
        // Sync Clerk email before /auth/me so ADMIN_EMAILS works without JWT email claims.
        let me: AuthUser | null = null;
        if (authUser.email) {
          me = await syncAuthProfile(authUser.email, authUser.name);
        } else {
          me = await fetchMe();
        }
        if (cancelled) return;
        onReady(
          me
            ? {
                ...me,
                email: me.email || authUser.email,
                name: me.name || authUser.name,
              }
            : authUser
        );
      } catch (e) {
        console.error("Clerk session bridge failed", e);
        if (!cancelled) onSignedOut();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isLoaded, isSignedIn, user, getToken, onReady, onSignedOut]);

  return null;
}

export default function App() {
  if (isClerkConfigured()) {
    return <ClerkApp />;
  }
  return <DevEmailApp />;
}

function ClerkApp() {
  const { signOut } = useClerk();
  const [authed, setAuthed] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [error, setError] = useState("");

  const onClerkReady = useCallback((me: AuthUser) => {
    setUser(me);
    setAuthed(true);
    setError("");
  }, []);

  const onClerkSignedOut = useCallback(() => {
    setUser(null);
    setAuthed(false);
  }, []);

  return (
    <>
      <ClerkSessionBridge onReady={onClerkReady} onSignedOut={onClerkSignedOut} />
      {!authed ? (
        <Landing onSignedIn={onClerkReady} />
      ) : (
        <StudioApp
          user={user}
          onLogout={async () => {
            clearSession();
            setUser(null);
            setAuthed(false);
            await signOut();
          }}
          clerkUserButton={<UserButton />}
          bootError={error}
          setBootError={setError}
        />
      )}
    </>
  );
}

function DevEmailApp() {
  const [authed, setAuthed] = useState(() => Boolean(getStoredToken()));
  const [user, setUser] = useState<AuthUser | null>(() => getStoredUser());
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!getStoredToken()) {
        setAuthed(false);
        setUser(null);
        return;
      }
      try {
        const me = await fetchMe();
        if (cancelled) return;
        if (!me) {
          setUser(null);
          setAuthed(false);
          return;
        }
        setUser(me);
        setAuthed(true);
      } catch (e) {
        if (!cancelled) {
          setError(String((e as Error).message || e));
          setAuthed(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!authed) {
    return (
      <>
        {error ? <p className="error-line">{error}</p> : null}
        <Landing
          onSignedIn={(me) => {
            setUser(me);
            setAuthed(true);
            setError("");
          }}
        />
      </>
    );
  }

  return (
    <StudioApp
      user={user}
      onLogout={async () => {
        await logout();
        clearSession();
        setUser(null);
        setAuthed(false);
      }}
      bootError={error}
      setBootError={setError}
    />
  );
}

function StudioApp({
  user,
  onLogout,
  clerkUserButton,
  bootError,
  setBootError,
}: {
  user: AuthUser | null;
  onLogout: () => void | Promise<void>;
  clerkUserButton?: React.ReactNode;
  bootError: string;
  setBootError: (v: string) => void;
}) {
  const [view, setView] = useState<View>("library");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [usage, setUsage] = useState<UsageSnapshot | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [step, setStep] = useState<Step>("upload");
  const [name, setName] = useState("");
  const [topic, setTopic] = useState("");
  const [recipe, setRecipe] = useState<StyleRecipe>("talking_head");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameOriginal, setRenameOriginal] = useState("");
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const pendingDurations = useRef(new WeakMap<File, number>());
  const [dragOver, setDragOver] = useState(false);
  const [clips, setClips] = useState<ClipInfo[]>([]);
  const [script, setScript] = useState("");
  const [completed, setCompleted] = useState<Partial<Record<Step, boolean>>>({});
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState(bootError);
  const [brollClips, setBrollClips] = useState<BrollClip[]>([]);
  const [reelStale, setReelStale] = useState(false);
  const [menuOpenIndex, setMenuOpenIndex] = useState<number | null>(null);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editMessage, setEditMessage] = useState("");
  const [editBusyIndex, setEditBusyIndex] = useState<number | null>(null);
  const [reelPreviewKey, setReelPreviewKey] = useState(0);
  const [coverPreviewKey, setCoverPreviewKey] = useState(0);
  const [exportPreview, setExportPreview] = useState<"reel" | "cover">("reel");
  const [selectedBrollIndex, setSelectedBrollIndex] = useState<number | null>(null);
  const [brollMediaUrls, setBrollMediaUrls] = useState<Record<number, string>>({});
  const [brollPosterUrls, setBrollPosterUrls] = useState<Record<number, string>>({});
  const [pendingPreviewUrl, setPendingPreviewUrl] = useState<string | null>(null);
  const [brandForm, setBrandForm] = useState<BrandProfile>(EMPTY_BRAND);
  const [brandLoaded, setBrandLoaded] = useState(false);
  const [brandBusy, setBrandBusy] = useState(false);
  const skipFeedbackClearOnMount = useRef(true);

  const clearFeedback = useCallback(() => {
    setError("");
    setStatus("");
    setBootError("");
  }, [setBootError]);

  useEffect(() => {
    if (bootError) setError(bootError);
  }, [bootError]);

  // Drop stale status/errors when leaving library ↔ wizard ↔ brand ↔ admin.
  // (Do not clear on every wizard step — stage runners set status/error after setStep.)
  useEffect(() => {
    if (skipFeedbackClearOnMount.current) {
      skipFeedbackClearOnMount.current = false;
      return;
    }
    clearFeedback();
  }, [view, clearFeedback]);

  function goToStep(next: Step) {
    if (next === step) return;
    clearFeedback();
    setStep(next);
  }

  const loadBrand = useCallback(async () => {
    const b = await getBrand();
    setBrandForm({ ...EMPTY_BRAND, ...b });
    setBrandLoaded(true);
  }, []);

  useEffect(() => {
    loadBrand().catch(() => {
      /* indicator stays until brand loads successfully */
    });
  }, [loadBrand]);

  const brandFilled = brandLoaded && brandIsFilled(brandForm);

  const loadBrollClips = useCallback(async (id: string, preferIndex?: number | null) => {
    try {
      const clipsList = await listBroll(id);
      setBrollClips(clipsList);
      const hasClips = clipsList.some(
        (c) =>
          Boolean(c.video_url || c.path) ||
          (c.source && c.source !== "skipped_keep_aroll")
      );
      // Prefer on-disk clips over run_report (stage list can be overwritten by later jobs).
      if (hasClips || clipsList.length > 0) {
        setCompleted((c) => ({ ...c, broll: true }));
      }
      const preferred =
        preferIndex != null
          ? clipsList.find(
              (c) => Number(c.broll_index) === preferIndex && (c.video_url || c.path)
            )
          : null;
      const firstWithVideo =
        preferred || clipsList.find((c) => c.video_url || c.path);
      setSelectedBrollIndex(
        firstWithVideo != null ? Number(firstWithVideo.broll_index) : null
      );
      return clipsList;
    } catch {
      setBrollClips([]);
      setSelectedBrollIndex(null);
      return [];
    }
  }, []);

  const refreshUsage = useCallback(async () => {
    try {
      setUsage(await fetchUsage());
    } catch {
      /* keep prior snapshot */
    }
  }, []);

  const refreshLibrary = useCallback(async () => {
    const [list] = await Promise.all([listProjects(), refreshUsage()]);
    setProjects(list);
  }, [refreshUsage]);

  const refreshProject = useCallback(async (id: string) => {
    const d = await getProject(id);
    setDetail(d);
    setClips(d.clips ?? []);
    const r = d.manifest?.recipe;
    if (r === "talking_head" || r === "tutorial" || r === "story") {
      setRecipe(r);
    } else {
      setRecipe("talking_head");
    }
    if (d.artifacts.script) {
      try {
        setScript(await getScript(id));
      } catch {
        /* ignore */
      }
    } else {
      setScript("");
    }
    setCompleted({
      upload: (d.clips?.length ?? 0) > 0,
      script: Boolean(d.artifacts.script && d.artifacts.plan),
      broll: Boolean(d.run_report?.stages?.some((s) => s.name === "broll" && s.status === "ok")),
      final: Boolean(d.artifacts.reel),
    });
    return d;
  }, []);

  useEffect(() => {
    refreshLibrary().catch((e) => {
      const msg = String((e as Error).message || e);
      setError(msg);
      setBootError(msg);
    });
  }, [refreshLibrary, setBootError]);

  // Re-fetch badges when returning to the library (pending/failed → completed).
  useEffect(() => {
    if (view !== "library") return;
    refreshLibrary().catch(() => undefined);
  }, [view, refreshLibrary]);

  async function onLogoutClick() {
    setProjects([]);
    setProjectId(null);
    setView("library");
    await onLogout();
  }

  useEffect(() => {
    if (pendingPreviewUrl?.startsWith("blob:")) URL.revokeObjectURL(pendingPreviewUrl);
    if (pendingFiles[0]) {
      setPendingPreviewUrl(URL.createObjectURL(pendingFiles[0]));
      return;
    }
    setPendingPreviewUrl(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingFiles]);

  const doneFlags = useMemo(
    () => ({
      upload: completed.upload || clips.length > 0,
      script: completed.script || Boolean(detail?.artifacts.script),
      broll:
        Boolean(completed.broll) ||
        brollClips.some((c) => Boolean(c.video_url || c.path)),
      final: completed.final || Boolean(detail?.artifacts.reel),
    }),
    [completed, clips.length, detail, brollClips]
  );

  const canRenderReel =
    doneFlags.upload &&
    doneFlags.script &&
    doneFlags.broll &&
    Boolean(detail?.artifacts.transcript || detail?.artifacts.plan);

  useEffect(() => {
    // Always hydrate from disk when visiting this step (run_report may omit broll stage).
    if (step === "broll" && projectId) {
      void loadBrollClips(projectId);
    }
  }, [step, projectId, loadBrollClips]);

  useEffect(() => {
    if (!projectId || brollClips.length === 0) {
      setBrollPosterUrls((prev) => {
        for (const u of Object.values(prev)) {
          if (u.startsWith("blob:")) URL.revokeObjectURL(u);
        }
        return {};
      });
      setBrollMediaUrls((prev) => {
        for (const u of Object.values(prev)) {
          if (u.startsWith("blob:")) URL.revokeObjectURL(u);
        }
        return {};
      });
      return;
    }
    let cancelled = false;
    (async () => {
      // Posters are tiny JPEGs — load all for instant list thumbs.
      await Promise.all(
        brollClips.map(async (c) => {
          const idx = Number(c.broll_index);
          if (!c.poster_url && !c.video_url) return;
          try {
            const url = await fetchMediaObjectUrl(
              `/projects/${projectId}/broll/${idx}/poster`
            );
            if (cancelled) {
              if (url.startsWith("blob:")) URL.revokeObjectURL(url);
              return;
            }
            setBrollPosterUrls((prev) => {
              if (prev[idx]) {
                if (url.startsWith("blob:")) URL.revokeObjectURL(url);
                return prev;
              }
              return { ...prev, [idx]: url };
            });
          } catch (e) {
            console.warn("B-roll poster load failed", idx, e);
          }
        })
      );
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, brollClips]);

  // Only fetch the full mp4 for the selected phone preview.
  useEffect(() => {
    if (!projectId || selectedBrollIndex == null) return;
    const clip = brollClips.find((c) => Number(c.broll_index) === selectedBrollIndex);
    if (!clip || (!clip.video_url && !clip.path)) return;
    if (brollMediaUrls[selectedBrollIndex]) return;
    let cancelled = false;
    (async () => {
      try {
        const url = await fetchMediaObjectUrl(
          `/projects/${projectId}/broll/${selectedBrollIndex}/video`
        );
        if (cancelled) {
          if (url.startsWith("blob:")) URL.revokeObjectURL(url);
          return;
        }
        setBrollMediaUrls((prev) => {
          if (prev[selectedBrollIndex]) {
            if (url.startsWith("blob:")) URL.revokeObjectURL(url);
            return prev;
          }
          return { ...prev, [selectedBrollIndex]: url };
        });
      } catch (e) {
        console.warn("B-roll media load failed", selectedBrollIndex, e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, selectedBrollIndex, brollClips, brollMediaUrls]);

  useEffect(() => {
    if (menuOpenIndex === null) return;
    const onDoc = () => setMenuOpenIndex(null);
    document.addEventListener("click", onDoc);
    return () => document.removeEventListener("click", onDoc);
  }, [menuOpenIndex]);

  const projectQuota = usage?.projects;
  const atProjectLimit = Boolean(
    projectQuota &&
      !projectQuota.unlimited &&
      projectQuota.limit != null &&
      projectQuota.used >= projectQuota.limit
  );
  const projectQuotaLabel = !projectQuota
    ? ""
    : projectQuota.unlimited || projectQuota.limit == null
      ? "Unlimited projects"
      : `${projectQuota.used} of ${projectQuota.limit} projects used`;

  async function onRecipeChange(next: StyleRecipe) {
    if (!projectId || next === recipe || busy) return;
    setError("");
    setBusy(true);
    setStatus("Updating style recipe…");
    try {
      await updateProject(projectId, { recipe: next });
      setRecipe(next);
      const d = await refreshProject(projectId);
      const lostPlan = !d.artifacts.plan;
      if (lostPlan && (completed.script || completed.broll || completed.final)) {
        setStatus("Recipe updated — re-run Extract script for the new pacing.");
        setStep("upload");
      } else {
        setStatus("");
      }
    } catch (e) {
      setError(String((e as Error).message || e));
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  async function onNewCut() {
    if (atProjectLimit) {
      setError(
        `Your plan includes ${projectQuota?.limit} project${
          projectQuota?.limit === 1 ? "" : "s"
        }. Upgrade to paid for more projects.`
      );
      return;
    }
    setError("");
    setBusy(true);
    setStatus("Creating project…");
    try {
      const created = await createProject(name.trim() || undefined, topic.trim() || undefined);
      setProjectId(created.id);
      setPendingFiles([]);
      setClips([]);
      setScript("");
      setBrollClips([]);
      setReelStale(false);
      setEditingIndex(null);
      setCompleted({});
      setRecipe("talking_head");
      setStep("upload");
      setView("wizard");
      await refreshProject(created.id);
      await refreshLibrary();
      setStatus("");
      setName("");
      setTopic("");
    } catch (e) {
      setError(String((e as Error).message || e));
      await refreshUsage();
    } finally {
      setBusy(false);
    }
  }

  function projectTitle(p: { id: string; name?: string | null }) {
    return (p.name || "").trim() || p.id;
  }

  function cancelRename() {
    setRenamingId(null);
    setRenameValue("");
    setRenameOriginal("");
  }

  async function saveRename(projectIdToRename: string) {
    const next = renameValue.trim();
    const prev = renameOriginal.trim();
    // Unchanged — just close the editor
    if (next === prev || (next === "" && prev === "")) {
      cancelRename();
      return;
    }
    try {
      await updateProject(projectIdToRename, { name: next || null });
      cancelRename();
      await refreshLibrary();
      if (projectId === projectIdToRename) {
        await refreshProject(projectIdToRename);
      }
    } catch {
      // Keep previous name; don't surface a hard error for a failed rename.
      cancelRename();
      await refreshLibrary().catch(() => undefined);
    }
  }

  async function openProject(id: string) {
    setError("");
    setProjectId(id);
    setView("wizard");
    setBusy(true);
    setReelStale(false);
    setEditingIndex(null);
    setBrollClips([]);
    try {
      const d = await refreshProject(id);
      // Restore clips from disk regardless of run_report stage history.
      await loadBrollClips(id);
      if (d.artifacts.reel) setStep("final");
      else if (d.artifacts.script) setStep("broll");
      else if ((d.clips?.length ?? 0) > 0) setStep("script");
      else setStep("upload");
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  }

  function movePending(i: number, dir: -1 | 1) {
    const j = i + dir;
    if (j < 0 || j >= pendingFiles.length) return;
    const next = [...pendingFiles];
    [next[i], next[j]] = [next[j], next[i]];
    setPendingFiles(next);
  }

  function clipsDurationSeconds() {
    return clips.reduce((sum, c) => sum + (c.duration_seconds || 0), 0);
  }

  function pendingDurationSeconds(files: File[] = pendingFiles) {
    return files.reduce((sum, f) => sum + (pendingDurations.current.get(f) || 0), 0);
  }

  async function addPendingFiles(incoming: File[]) {
    if (!incoming.length) return;
    setError("");
    const existing = clipsDurationSeconds() + pendingDurationSeconds();
    const { ok, error: err } = await validateVideoFiles(incoming, existing);
    if (err) {
      setError(err);
      return;
    }
    for (const item of ok) {
      pendingDurations.current.set(item.file, item.duration);
    }
    setPendingFiles((prev) => [...prev, ...ok.map((x) => x.file)]);
    setStatus("");
  }

  const sequenceLocked = Boolean(detail?.artifacts.reel) || doneFlags.final;

  function moveClip(i: number, dir: -1 | 1) {
    if (sequenceLocked) return;
    const j = i + dir;
    if (j < 0 || j >= clips.length) return;
    const next = [...clips];
    [next[i], next[j]] = [next[j], next[i]];
    setClips(next);
  }

  async function removeUploadedClip(filename: string) {
    if (!projectId) return;
    if (sequenceLocked) {
      setError("This reel is finalized. Clip remove and reorder are locked.");
      return;
    }
    const derived =
      Boolean(detail?.artifacts.script) ||
      Boolean(detail?.artifacts.plan) ||
      Boolean(detail?.artifacts.merged) ||
      doneFlags.script ||
      doneFlags.broll;
    const msg = derived
      ? "Remove this clip? Script, B-roll, and final export for this cut will be cleared — you’ll need to extract the script again."
      : "Remove this clip?";
    if (!window.confirm(msg)) return;

    setError("");
    setBusy(true);
    try {
      const res = await deleteClip(projectId, filename);
      setClips(res.clips);
      if (res.invalidated) {
        setScript("");
        setBrollClips([]);
        setReelStale(false);
        setEditingIndex(null);
        setCompleted({ upload: res.clips.length > 0 });
        setStatus("Clip removed. Re-run Extract script for the new sequence.");
        setStep("upload");
      } else {
        setStatus(res.clips.length ? "Clip removed." : "All clips removed.");
      }
      await refreshProject(projectId);
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  }

  async function saveSequenceAndContinue() {
    if (!projectId) return;
    if (sequenceLocked) {
      setError("This reel is finalized. Clip remove and reorder are locked.");
      return;
    }
    setError("");
    setBusy(true);
    try {
      if (pendingFiles.length) {
        setStatus("Uploading clips…");
        const res = await uploadClips(projectId, pendingFiles, clips.length === 0);
        setClips(res.clips);
        setPendingFiles([]);
      } else if (clips.length) {
        setStatus("Saving sequence…");
        const res = await reorderClips(
          projectId,
          clips.map((c) => c.filename)
        );
        setClips(res.clips);
      } else {
        throw new Error("Add at least one clip");
      }
      await refreshProject(projectId);
      setCompleted((c) => ({ ...c, upload: true }));
      setStatus("");
      setStep("script");
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  }

  async function runStage(from: string, to: string, next: Step, label: string) {
    if (!projectId) return;
    setError("");
    setBusy(true);
    setStatus(`${label}…`);
    try {
      const job = await startJob(projectId, from, to);
      await pollJobUntilDone(projectId, job.job_id, (st) => {
        setStatus(`${label}: ${st}`);
      });
      const d = await refreshProject(projectId);
      if (to === "plan" || from === "merge") {
        try {
          setScript(await getScript(projectId));
        } catch {
          /* ignore */
        }
        setCompleted((c) => ({ ...c, script: true }));
      }
      if (to === "broll") {
        setCompleted((c) => ({ ...c, broll: true }));
        await loadBrollClips(projectId);
        const skips = (d.run_report?.broll ?? []).filter(
          (b) => b.source === "skipped_keep_aroll"
        );
        const veoUsed = d.run_report?.veo_clips_used ?? 0;
        if (skips.length && veoUsed === 0) {
          setError(
            "B-roll finished with no cutaway clips. Check your API quota/billing, then try Generate again."
          );
        }
      }
      if (to === "render" || to === "cover") {
        setCompleted((c) => ({ ...c, final: true }));
        if (to === "render") {
          setReelStale(false);
          setReelPreviewKey((k) => k + 1);
        }
        if (to === "cover") {
          setCoverPreviewKey((k) => k + 1);
          setExportPreview("cover");
        }
      }
      setStep(next);
      setStatus(`${label} complete.`);
      await refreshLibrary();
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  }

  async function submitBrollEdit() {
    if (!projectId || editingIndex === null) return;
    const message = editMessage.trim();
    if (!message) {
      setError("Enter an edit description for this B-roll clip.");
      return;
    }
    setError("");
    setEditBusyIndex(editingIndex);
    setStatus(`Regenerating B-roll ${String(editingIndex).padStart(3, "0")}…`);
    try {
      await editBroll(projectId, editingIndex, message);
      await loadBrollClips(projectId, editingIndex);
      setSelectedBrollIndex(editingIndex);
      setReelStale(true);
      setEditingIndex(null);
      setEditMessage("");
      setStatus("B-roll updated. Re-render the reel to apply it.");
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setEditBusyIndex(null);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-row">
          <div className="logo">
            <div className="logo-mark">⚡</div>
            ReelKut
          </div>
          <button
            type="button"
            className={`nav-link ${view === "library" ? "active" : ""}`}
            onClick={() => {
              setView("library");
              clearFeedback();
              refreshLibrary().catch(() => undefined);
            }}
          >
            Library
          </button>
          <button
            type="button"
            className={`nav-link ${view === "brand" ? "active" : ""}`}
            onClick={() => {
              setView("brand");
              clearFeedback();
              loadBrand().catch((e) => setError(String((e as Error).message || e)));
            }}
          >
            Brand
            {brandLoaded && !brandFilled ? (
              <span
                className="nav-incomplete"
                title="Brand details not filled in yet"
                aria-label="Brand details not filled in yet"
              >
                !
              </span>
            ) : null}
          </button>
          {user?.is_admin ? (
            <button
              type="button"
              className={`nav-link ${view === "admin" ? "active" : ""}`}
              onClick={() => {
                setView("admin");
                clearFeedback();
              }}
            >
              Admin
            </button>
          ) : null}
        </div>
        <div className="top-actions">
          {clerkUserButton ? (
            clerkUserButton
          ) : (
            <>
              <span className="user-chip" title={user?.email || ""}>
                {user?.name || user?.email}
              </span>
              <button type="button" className="btn btn-ghost" onClick={() => void onLogoutClick()}>
                Sign out
              </button>
            </>
          )}
          <button
            type="button"
            className="btn btn-ghost"
            disabled={busy}
            onClick={() => {
              setView("library");
              clearFeedback();
            }}
          >
            + New Cut
          </button>
        </div>
      </header>

      <main className="main">
        {view === "admin" && user?.is_admin ? (
          <>
            {(status || error) && (
              <div className="page-feedback" role="status" aria-live="polite">
                {status ? <p className="status-line">{status}</p> : null}
                {error ? <p className="error-line">{error}</p> : null}
              </div>
            )}
            <Admin onError={setError} onStatus={setStatus} />
          </>
        ) : view === "brand" ? (
          <div className="card">
            <div className="library-hero">
              <h1 className="page-title">Brand</h1>
              <p className="page-sub">
                Set once for your account. This drives B-roll look and casting; craft prompts stay generic.
                Save before creating reels.
              </p>
              {!brandLoaded ? (
                <p className="muted">Loading brand…</p>
              ) : (
                <form
                  className="brand-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void (async () => {
                      setBrandBusy(true);
                      setError("");
                      setStatus("");
                      try {
                        const saved = await saveBrand(brandForm);
                        setBrandForm({ ...EMPTY_BRAND, ...saved });
                        setStatus("Brand saved.");
                      } catch (err) {
                        setError(String((err as Error).message || err));
                      } finally {
                        setBrandBusy(false);
                      }
                    })();
                  }}
                >
                  <div className="form-row">
                    <div className="field">
                      <label htmlFor="brand-niche">Niche</label>
                      <input
                        id="brand-niche"
                        placeholder="e.g. fitness coaching"
                        value={brandForm.niche || ""}
                        onChange={(e) => setBrandForm((b) => ({ ...b, niche: e.target.value }))}
                      />
                    </div>
                    <div className="field">
                      <label htmlFor="brand-audience">Audience</label>
                      <input
                        id="brand-audience"
                        placeholder="Who this is for"
                        value={brandForm.audience || ""}
                        onChange={(e) => setBrandForm((b) => ({ ...b, audience: e.target.value }))}
                      />
                    </div>
                  </div>
                  <div className="form-row">
                    <div className="field field-wide">
                      <label htmlFor="brand-setting">Setting / visual world</label>
                      <textarea
                        id="brand-setting"
                        className="brand-textarea"
                        rows={2}
                        placeholder="Locations and atmosphere for B-roll"
                        value={brandForm.setting || ""}
                        onChange={(e) => setBrandForm((b) => ({ ...b, setting: e.target.value }))}
                      />
                    </div>
                  </div>
                  <div className="form-row">
                    <div className="field field-wide">
                      <label htmlFor="brand-casting">B-roll casting</label>
                      <textarea
                        id="brand-casting"
                        className="brand-textarea"
                        rows={2}
                        placeholder="People look, wardrobe, consistency across cutaways"
                        value={brandForm.broll_casting || ""}
                        onChange={(e) =>
                          setBrandForm((b) => ({ ...b, broll_casting: e.target.value }))
                        }
                      />
                    </div>
                  </div>
                  <div className="form-row">
                    <div className="field">
                      <label htmlFor="brand-mood">Mood</label>
                      <input
                        id="brand-mood"
                        value={brandForm.mood || ""}
                        onChange={(e) => setBrandForm((b) => ({ ...b, mood: e.target.value }))}
                      />
                    </div>
                    <div className="field">
                      <label htmlFor="brand-tone">Visual tone</label>
                      <input
                        id="brand-tone"
                        value={brandForm.visual_tone || ""}
                        onChange={(e) =>
                          setBrandForm((b) => ({ ...b, visual_tone: e.target.value }))
                        }
                      />
                    </div>
                    <div className="field">
                      <label htmlFor="brand-palette">Color palette</label>
                      <input
                        id="brand-palette"
                        value={brandForm.color_palette || ""}
                        onChange={(e) =>
                          setBrandForm((b) => ({ ...b, color_palette: e.target.value }))
                        }
                      />
                    </div>
                  </div>
                  <div className="form-row">
                    <div className="field field-wide">
                      <label htmlFor="brand-avoid">Avoid</label>
                      <textarea
                        id="brand-avoid"
                        className="brand-textarea"
                        rows={2}
                        placeholder="Things Veo / Gemini should not invent"
                        value={brandForm.avoid || ""}
                        onChange={(e) => setBrandForm((b) => ({ ...b, avoid: e.target.value }))}
                      />
                    </div>
                  </div>
                  <div className="form-row">
                    <button className="btn btn-primary" type="submit" disabled={brandBusy || busy}>
                      {brandBusy ? "Saving…" : "Save brand"}
                    </button>
                  </div>
                  {status ? <p className="status-line">{status}</p> : null}
                  {error ? <p className="error-line">{error}</p> : null}
                </form>
              )}
            </div>
          </div>
        ) : view === "library" ? (
          <div className="card">
            <div className="library-hero">
              <h1 className="page-title">Your cuts</h1>
              <p className="page-sub">Create a new cut or reopen a past reel.</p>
              {projectQuotaLabel ? (
                <p
                  className={`library-quota${atProjectLimit ? " is-limit" : ""}`}
                  title={
                    atProjectLimit
                      ? "Lifetime free-project limit — deleting a cut does not free a slot"
                      : undefined
                  }
                >
                  {projectQuotaLabel}
                  {atProjectLimit
                    ? " — upgrade to paid for more projects"
                    : projectQuota?.plan === "paid"
                      ? " (paid)"
                      : ""}
                </p>
              ) : null}
              <div className="form-row">
                <div className="field">
                  <label htmlFor="name">Name</label>
                  <input
                    id="name"
                    placeholder="e.g. Q2 launch reel"
                    value={name}
                    disabled={atProjectLimit}
                    onChange={(e) => {
                      setName(e.target.value);
                      if (error) clearFeedback();
                    }}
                  />
                </div>
                <div className="field">
                  <label htmlFor="topic">Intent</label>
                  <input
                    id="topic"
                    placeholder="B-roll theme only (not dialogue)"
                    value={topic}
                    disabled={atProjectLimit}
                    onChange={(e) => {
                      setTopic(e.target.value);
                      if (error) clearFeedback();
                    }}
                  />
                </div>
                <button
                  className="btn btn-primary"
                  type="button"
                  disabled={busy || atProjectLimit}
                  title={
                    atProjectLimit
                      ? "Your plan includes a limited number of projects. Upgrade to paid for more."
                      : undefined
                  }
                  onClick={onNewCut}
                >
                  Create cut
                </button>
              </div>
              {atProjectLimit && !error ? (
                <p className="error-line">
                  Your plan includes {projectQuota?.limit} project
                  {projectQuota?.limit === 1 ? "" : "s"}. Upgrade to paid for more projects.
                </p>
              ) : null}
              {status ? <p className="status-line">{status}</p> : null}
              {error ? <p className="error-line">{error}</p> : null}
            </div>
            <div className="library-grid">
              {!projects.length ? (
                <p className="muted">No projects yet.</p>
              ) : (
                projects.map((p) => {
                  const badge = projectStatusBadge(p);
                  const title = projectTitle(p);
                  return (
                    <div
                      key={p.id}
                      className="project-card"
                      role="button"
                      tabIndex={0}
                      aria-label={`Open ${title}`}
                      onClick={() => {
                        if (renamingId === p.id) return;
                        void openProject(p.id);
                      }}
                      onKeyDown={(e) => {
                        if (renamingId === p.id) return;
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          void openProject(p.id);
                        }
                      }}
                    >
                      <div className="project-card-body">
                        <div className="project-card-copy">
                          {renamingId === p.id ? (
                            <input
                              className="project-rename-input"
                              value={renameValue}
                              autoFocus
                              onClick={(e) => e.stopPropagation()}
                              onChange={(e) => setRenameValue(e.target.value)}
                              onKeyDown={(e) => {
                                e.stopPropagation();
                                if (e.key === "Enter") {
                                  e.preventDefault();
                                  void saveRename(p.id);
                                }
                                if (e.key === "Escape") {
                                  e.preventDefault();
                                  cancelRename();
                                }
                              }}
                              onBlur={() => void saveRename(p.id)}
                              aria-label="Project name"
                            />
                          ) : (
                            <div className="project-title-row">
                              <h3>{title}</h3>
                              <button
                                type="button"
                                className="icon-btn project-pencil"
                                title="Rename"
                                aria-label={`Rename ${title}`}
                                disabled={busy}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setRenamingId(p.id);
                                  const current = (p.name || "").trim();
                                  setRenameValue(current);
                                  setRenameOriginal(current);
                                  setStatus("");
                                  setError("");
                                }}
                              >
                                ✎
                              </button>
                            </div>
                          )}
                          <p>
                            {p.clip_count} clip{p.clip_count === 1 ? "" : "s"}
                            {p.topic ? ` · ${p.topic}` : ""}
                            {p.updated_at
                              ? ` · ${new Date(p.updated_at).toLocaleString()}`
                              : ""}
                          </p>
                          <p className="project-id-line">{p.id}</p>
                        </div>
                        <span
                          className={`badge badge-${badge.kind}`}
                          title={
                            badge.kind === "failed"
                              ? "Last run failed — open to retry"
                              : undefined
                          }
                        >
                          {badge.label}
                        </span>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        ) : (
          <div className="card">
            <div className="stepper">
              <div className="stepper-steps">
                {STEPS.map((s) => {
                  const isActive = step === s.id;
                  // Only real completion — don't mark earlier steps done just because we're ahead.
                  const isDone = Boolean(doneFlags[s.id]);
                  return (
                    <button
                      key={s.id}
                      type="button"
                      className={`step ${isActive ? "active" : ""} ${isDone && !isActive ? "done" : ""}`}
                      onClick={() => goToStep(s.id)}
                      aria-current={isActive ? "step" : undefined}
                      title={isDone ? `${s.label} — completed` : s.label}
                    >
                      <span className="num" aria-hidden>
                        {isActive ? s.num : isDone ? "✓" : s.num}
                      </span>
                      <span className="step-label">{s.label}</span>
                    </button>
                  );
                })}
              </div>
              {doneFlags.final ? (
                <span className="stepper-complete" title="This cut has a finished reel">
                  <span className="stepper-complete-mark" aria-hidden>
                    ✓
                  </span>
                  Completed
                </span>
              ) : null}
            </div>

            {step === "upload" ? (
              <>
                <div className="workspace">
                  <div className="workspace-main">
                    <h2 className="page-title">Sequence your clips</h2>
                    <p className="page-sub">
                      Arrange clips in the order you want them merged. The full sequence
                      can’t exceed {MAX_PROJECT_SECONDS} seconds.
                    </p>

                    {projectId ? (
                      <div className="recipe-picker" role="group" aria-label="Style recipe">
                        <p className="recipe-picker-label">Style recipe</p>
                        <p className="page-sub recipe-picker-hint">
                          Automatic pacing and zoom — not a timeline editor. Default matches
                          previous builds.
                        </p>
                        <div className="recipe-options">
                          {STYLE_RECIPES.map((opt) => (
                            <button
                              key={opt.id}
                              type="button"
                              className={`recipe-option${recipe === opt.id ? " is-active" : ""}`}
                              disabled={busy}
                              onClick={() => void onRecipeChange(opt.id)}
                            >
                              <strong>{opt.label}</strong>
                              <span>{opt.blurb}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    <ul className="clip-list">
                      {pendingFiles.map((f, i) => (
                        <li className="clip-row" key={`p-${f.name}-${i}`}>
                          <span className="grip" aria-hidden>
                            ⠿
                          </span>
                          <span className="idx">{String(i + 1).padStart(2, "0")}</span>
                          <div className="thumb-ph" aria-hidden />
                          <div className="clip-meta">
                            <div className="clip-name">{f.name}</div>
                            <div className="clip-dur">
                              {formatSeconds(pendingDurations.current.get(f) || 0)} ·{" "}
                              {formatBytes(f.size)} · pending
                            </div>
                          </div>
                          <div className="icon-btns">
                            <button type="button" className="icon-btn" onClick={() => movePending(i, -1)} disabled={i === 0}>
                              ↑
                            </button>
                            <button
                              type="button"
                              className="icon-btn"
                              onClick={() => movePending(i, 1)}
                              disabled={i === pendingFiles.length - 1}
                            >
                              ↓
                            </button>
                            <button
                              type="button"
                              className="icon-btn"
                              onClick={() => setPendingFiles(pendingFiles.filter((_, j) => j !== i))}
                            >
                              ⌫
                            </button>
                          </div>
                        </li>
                      ))}

                      {clips.map((c, i) => (
                        <li className="clip-row" key={c.filename}>
                          <span className="grip" aria-hidden>
                            ⠿
                          </span>
                          <span className="idx">{String(c.index).padStart(2, "0")}</span>
                          <div className="thumb-ph" aria-hidden />
                          <div className="clip-meta">
                            <div className="clip-name">{c.filename}</div>
                            <div className="clip-dur">
                              {c.duration_seconds != null
                                ? formatSeconds(c.duration_seconds)
                                : "—"}{" "}
                              · {formatBytes(c.size_bytes)}
                            </div>
                          </div>
                          <div className="icon-btns">
                            <button
                              type="button"
                              className="icon-btn"
                              onClick={() => moveClip(i, -1)}
                              disabled={busy || sequenceLocked || i === 0}
                              title={sequenceLocked ? "Sequence locked after final export" : "Move up"}
                            >
                              ↑
                            </button>
                            <button
                              type="button"
                              className="icon-btn"
                              onClick={() => moveClip(i, 1)}
                              disabled={busy || sequenceLocked || i === clips.length - 1}
                              title={sequenceLocked ? "Sequence locked after final export" : "Move down"}
                            >
                              ↓
                            </button>
                            <button
                              type="button"
                              className="icon-btn"
                              title={
                                sequenceLocked
                                  ? "Sequence locked after final export"
                                  : "Remove clip"
                              }
                              aria-label={`Remove ${c.filename}`}
                              disabled={busy || sequenceLocked}
                              onClick={() => void removeUploadedClip(c.filename)}
                            >
                              ⌫
                            </button>
                          </div>
                        </li>
                      ))}
                    </ul>

                    {sequenceLocked ? (
                      <p className="clip-total muted">
                        Final reel exists — clip remove and reorder are locked.
                      </p>
                    ) : null}

                    {(pendingFiles.length > 0 || clips.length > 0) && (
                      <p className="clip-total muted">
                        Sequence total:{" "}
                        {Math.round(clipsDurationSeconds() + pendingDurationSeconds())} of{" "}
                        {MAX_PROJECT_SECONDS} seconds
                      </p>
                    )}

                    <label
                      className={`add-drop${dragOver ? " is-dragover" : ""}`}
                      onDragEnter={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setDragOver(true);
                      }}
                      onDragOver={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setDragOver(true);
                      }}
                      onDragLeave={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setDragOver(false);
                      }}
                      onDrop={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setDragOver(false);
                        const files = Array.from(e.dataTransfer.files || []).filter((f) =>
                          /\.(mp4|mov)$/i.test(f.name) || f.type.startsWith("video/")
                        );
                        if (files.length) void addPendingFiles(files);
                      }}
                    >
                      <input
                        type="file"
                        accept="video/mp4,video/quicktime,.mp4,.mov"
                        multiple
                        hidden
                        onChange={(e) => {
                          if (e.target.files?.length) {
                            void addPendingFiles(Array.from(e.target.files));
                          }
                          e.target.value = "";
                        }}
                      />
                      <span className="add-drop-row">
                        <span className="btn btn-file">
                          {pendingFiles.length || clips.length ? "Add files" : "Select files"}
                        </span>
                        <span className="add-drop-hint">or drop them here</span>
                      </span>
                      <span className="add-drop-meta">
                        MP4 or MOV · up to {MAX_CLIP_SECONDS} seconds per clip ·{" "}
                        {MAX_PROJECT_SECONDS} seconds total
                      </span>
                    </label>
                  </div>

                  <aside className="workspace-side">
                    <PhoneVideo
                      localSrc={pendingPreviewUrl}
                      srcPath={
                        !pendingPreviewUrl && projectId && clips[0]
                          ? `/projects/${projectId}/clips/${encodeURIComponent(clips[0].filename)}`
                          : null
                      }
                      cacheKey={clips[0]?.filename || pendingFiles[0]?.name || ""}
                      emptyText="Preview appears after you add a clip"
                    />
                    <p className="preview-time">9:16 preview</p>
                  </aside>
                </div>

                <div className="footer-bar">
                  <button
                    type="button"
                    className="btn-text"
                    onClick={() => {
                      clearFeedback();
                      setView("library");
                    }}
                  >
                    Save draft
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={
                      busy ||
                      sequenceLocked ||
                      (!pendingFiles.length && !clips.length)
                    }
                    onClick={saveSequenceAndContinue}
                    title={
                      sequenceLocked
                        ? "Sequence locked after final export"
                        : undefined
                    }
                  >
                    Save sequence & continue →
                  </button>
                </div>
              </>
            ) : null}

            {step === "script" ? (
              <>
                <div className="single-pane">
                  <h2 className="page-title">Extract script</h2>
                  <p className="page-sub">
                    We’ll clean up your audio, turn your speech into a script, and plan the cuts — without changing what you said, so your lips stay in sync.
                  </p>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={busy || !doneFlags.upload}
                    onClick={() => runStage("merge", "plan", "broll", "Extracting script")}
                  >
                    Extract script
                  </button>
                  {script ? (
                    <>
                      <h3 style={{ marginTop: "1.25rem" }}>Extracted script</h3>
                      <div className="script-box">{script}</div>
                    </>
                  ) : null}
                </div>
                <div className="footer-bar">
                  <button type="button" className="btn-text" onClick={() => goToStep("upload")}>
                    ← Back
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={!doneFlags.script || busy}
                    onClick={() => goToStep("broll")}
                  >
                    Continue →
                  </button>
                </div>
              </>
            ) : null}

            {step === "broll" ? (
              <>
                <div className="workspace">
                  <div className="workspace-main">
                    <h2 className="page-title">Generate B-roll</h2>
                    <p className="page-sub">
                      We’ll create short cutaway clips that match what you’re saying. When they’re
                      ready, review each one. Open the ⋮ menu and choose Edit if you want to change a
                      clip.
                    </p>
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={busy || !doneFlags.script}
                      onClick={() => runStage("align", "broll", "broll", "Generating B-roll")}
                    >
                      {brollClips.some((c) => c.video_url || c.path)
                        ? "Regenerate B-roll"
                        : "Generate B-roll"}
                    </button>

                    {reelStale && detail?.artifacts.reel ? (
                      <p className="stale-banner" role="status">
                        B-roll changed — update the reel to include your edits.
                      </p>
                    ) : null}

                    {brollClips.length > 0 ? (
                      <ul className="broll-list">
                        {brollClips.map((clip) => {
                          const idx = Number(clip.broll_index);
                          const hasVideo = Boolean(
                            clip.video_url || brollMediaUrls[idx]
                          );
                          const isEditing = editingIndex === idx;
                          const isBusy = editBusyIndex === idx;
                          const isSelected = selectedBrollIndex === idx;
                          return (
                            <li
                              key={idx}
                              className={`broll-row${isSelected ? " broll-row-selected" : ""}`}
                              onClick={() => {
                                if (hasVideo) setSelectedBrollIndex(idx);
                              }}
                            >
                              <div className="broll-thumb">
                                {brollPosterUrls[idx] || brollMediaUrls[idx] ? (
                                  brollPosterUrls[idx] ? (
                                    <img
                                      key={`${idx}-poster-${clip.versions?.length ?? 0}`}
                                      src={brollPosterUrls[idx]}
                                      alt=""
                                    />
                                  ) : (
                                    <video
                                      key={`${idx}-${clip.versions?.length ?? 0}-${brollMediaUrls[idx]}`}
                                      src={brollMediaUrls[idx]}
                                      muted
                                      playsInline
                                      preload="metadata"
                                    />
                                  )
                                ) : (
                                  <div className="broll-thumb-ph">
                                    {hasVideo ? "…" : "No file"}
                                  </div>
                                )}
                              </div>
                              <div className="broll-meta">
                                <div className="broll-title-row">
                                  <span className="idx">{String(idx).padStart(2, "0")}</span>
                                  <span className="broll-clip-label">Cutaway</span>
                                </div>
                                <p className="broll-desc">
                                  {clip.suggestion || "No description"}
                                </p>
                                {clip.spoken_text ? (
                                  <p className="broll-spoken">{clip.spoken_text}</p>
                                ) : null}
                                {clip.detail && clip.source === "skipped_keep_aroll" ? (
                                  <p className="broll-detail">{clip.detail}</p>
                                ) : null}

                                {isEditing ? (
                                  <div
                                    className="broll-edit-panel"
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    <label className="field-label" htmlFor={`broll-edit-${idx}`}>
                                      Describe the change
                                    </label>
                                    <textarea
                                      id={`broll-edit-${idx}`}
                                      className="broll-edit-input"
                                      rows={3}
                                      value={editMessage}
                                      disabled={isBusy}
                                      placeholder="e.g. warmer light, slower camera move, more close-up…"
                                      onChange={(e) => setEditMessage(e.target.value)}
                                    />
                                    <p className="broll-edit-safety muted">
                                      Keep edits safe — no nudity, sexual, violent, hateful, or
                                      abusive content.
                                    </p>
                                    <div className="form-row">
                                      <button
                                        type="button"
                                        className="btn btn-primary"
                                        disabled={isBusy || busy}
                                        onClick={() => void submitBrollEdit()}
                                      >
                                        {isBusy ? "Regenerating…" : "Apply edit"}
                                      </button>
                                      <button
                                        type="button"
                                        className="btn btn-ghost"
                                        disabled={isBusy}
                                        onClick={() => {
                                          setEditingIndex(null);
                                          setEditMessage("");
                                        }}
                                      >
                                        Cancel
                                      </button>
                                    </div>
                                  </div>
                                ) : null}
                              </div>
                              <div
                                className="broll-menu-wrap"
                                onClick={(e) => e.stopPropagation()}
                              >
                                <button
                                  type="button"
                                  className="broll-kebab"
                                  aria-label={`Options for B-roll ${idx}`}
                                  disabled={busy || isBusy}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setMenuOpenIndex(menuOpenIndex === idx ? null : idx);
                                  }}
                                >
                                  ⋮
                                </button>
                                {menuOpenIndex === idx ? (
                                  <div className="broll-menu" role="menu">
                                    <button
                                      type="button"
                                      role="menuitem"
                                      disabled={!hasVideo}
                                      title={
                                        hasVideo
                                          ? "Edit this clip"
                                          : "No video — skipped clips can’t be edited"
                                      }
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        setMenuOpenIndex(null);
                                        if (!hasVideo) return;
                                        setEditingIndex(idx);
                                        setEditMessage("");
                                        setSelectedBrollIndex(idx);
                                      }}
                                    >
                                      Edit
                                    </button>
                                  </div>
                                ) : null}
                              </div>
                            </li>
                          );
                        })}
                      </ul>
                    ) : null}
                  </div>
                  <aside className="workspace-side">
                    <PhoneVideo
                      localSrc={
                        selectedBrollIndex != null
                          ? brollMediaUrls[selectedBrollIndex] || null
                          : null
                      }
                      posterSrc={
                        selectedBrollIndex != null
                          ? brollPosterUrls[selectedBrollIndex] || null
                          : null
                      }
                      srcPath={
                        projectId &&
                        selectedBrollIndex != null &&
                        !brollMediaUrls[selectedBrollIndex] &&
                        brollClips.some(
                          (c) =>
                            Number(c.broll_index) === selectedBrollIndex &&
                            (c.video_url || c.path)
                        )
                          ? `/projects/${projectId}/broll/${selectedBrollIndex}/video`
                          : null
                      }
                      cacheKey={`${selectedBrollIndex}-${
                        brollClips.find((c) => Number(c.broll_index) === selectedBrollIndex)
                          ?.versions?.length ?? 0
                      }`}
                      emptyText={
                        brollClips.length
                          ? "Select a cutaway to preview"
                          : "Generate B-roll to preview cutaways"
                      }
                    />
                  </aside>
                </div>
                <div className="footer-bar">
                  <button type="button" className="btn-text" onClick={() => goToStep("script")}>
                    ← Back
                  </button>
                  <div className="form-row">
                    {reelStale || detail?.artifacts.reel ? (
                      <button
                        type="button"
                        className="btn btn-ghost"
                        disabled={busy || !doneFlags.broll}
                        onClick={() =>
                          runStage(
                            "render",
                            "render",
                            "final",
                            reelStale ? "Updating reel" : "Re-rendering reel"
                          )
                        }
                      >
                        {reelStale ? "Update reel" : "Re-render reel"}
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={!doneFlags.broll || busy}
                      onClick={() => goToStep("final")}
                    >
                      Continue →
                    </button>
                  </div>
                </div>
              </>
            ) : null}

            {step === "final" ? (
              <>
                <div className="workspace">
                  <div className="workspace-main">
                    <h2 className="page-title">Final export</h2>
                    <p className="page-sub">
                      Render the vertical reel (uses current B-roll files), then optionally generate a
                      cover.
                    </p>
                    {reelStale ? (
                      <p className="stale-banner" role="status">
                        B-roll was edited — render again to refresh the reel.
                      </p>
                    ) : null}
                    <div className="export-actions">
                      <button
                        type="button"
                        className="btn btn-primary"
                        disabled={busy || !canRenderReel}
                        title={
                          canRenderReel
                            ? undefined
                            : "Finish Upload, Extract script, and Generate B-roll first"
                        }
                        onClick={() =>
                          runStage(
                            detail?.artifacts.reel || reelStale ? "render" : "subtitles",
                            "render",
                            "final",
                            reelStale ? "Updating reel" : "Rendering reel"
                          )
                        }
                      >
                        {reelStale
                          ? "Update reel"
                          : detail?.artifacts.reel
                            ? "Re-render reel"
                            : "Render reel"}
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost"
                        disabled={busy || !doneFlags.final}
                        onClick={() => runStage("cover", "cover", "final", "Generating cover")}
                      >
                        Generate cover
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost"
                        disabled={busy || !detail?.artifacts.reel}
                        title={
                          detail?.artifacts.reel ? undefined : "Render the reel first"
                        }
                        onClick={() => {
                          if (!projectId || !detail?.artifacts.reel) return;
                          void downloadAuthenticated(
                            `/projects/${projectId}/artifacts/reel`,
                            "reel.mp4"
                          ).catch((e) => setError(String((e as Error).message || e)));
                        }}
                      >
                        Download reel
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost"
                        disabled={busy || !detail?.artifacts.cover}
                        title={
                          detail?.artifacts.cover ? undefined : "Generate a cover first"
                        }
                        onClick={() => {
                          if (!projectId || !detail?.artifacts.cover) return;
                          void downloadAuthenticated(
                            `/projects/${projectId}/artifacts/cover`,
                            "cover.jpg"
                          ).catch((e) => setError(String((e as Error).message || e)));
                        }}
                      >
                        Download cover
                      </button>
                    </div>
                    {!canRenderReel ? (
                      <p className="muted" style={{ marginTop: "0.75rem" }}>
                        {!doneFlags.upload
                          ? "Add clips in Upload & Sequence before rendering."
                          : !doneFlags.script ||
                              !(detail?.artifacts.transcript || detail?.artifacts.plan)
                            ? "Extract the script first — render needs the transcript."
                            : "Generate B-roll first, then render the reel."}
                      </p>
                    ) : null}
                  </div>
                  <aside className="workspace-side export-preview-carousel">
                    {exportPreview === "cover" ? (
                      <PhoneStill
                        srcPath={
                          projectId && detail?.artifacts.cover
                            ? `/projects/${projectId}/artifacts/cover`
                            : null
                        }
                        cacheKey={coverPreviewKey}
                        emptyText="Generate cover to preview"
                      />
                    ) : (
                      <PhoneVideo
                        srcPath={
                          projectId && detail?.artifacts.reel
                            ? `/projects/${projectId}/artifacts/reel`
                            : null
                        }
                        cacheKey={reelPreviewKey}
                        emptyText="Render to preview your reel"
                      />
                    )}
                    <div className="preview-switch" role="tablist" aria-label="Export preview">
                      <button
                        type="button"
                        role="tab"
                        aria-selected={exportPreview === "reel"}
                        className={`preview-switch-btn${exportPreview === "reel" ? " is-active" : ""}`}
                        onClick={() => setExportPreview("reel")}
                      >
                        <span
                          className={`preview-dot${exportPreview === "reel" ? " is-active" : ""}${
                            detail?.artifacts.reel ? " has-media" : ""
                          }`}
                          aria-hidden
                        />
                        Reel
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={exportPreview === "cover"}
                        className={`preview-switch-btn${exportPreview === "cover" ? " is-active" : ""}`}
                        onClick={() => setExportPreview("cover")}
                      >
                        <span
                          className={`preview-dot${exportPreview === "cover" ? " is-active" : ""}${
                            detail?.artifacts.cover ? " has-media" : ""
                          }`}
                          aria-hidden
                        />
                        Cover
                        {detail?.artifacts.cover ? (
                          <span className="preview-ready" aria-label="Cover ready">
                            ✓
                          </span>
                        ) : null}
                      </button>
                    </div>
                  </aside>
                </div>
                <div className="footer-bar">
                  <button type="button" className="btn-text" onClick={() => goToStep("broll")}>
                    ← Back
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() => {
                      clearFeedback();
                      setView("library");
                    }}
                  >
                    Back to library
                  </button>
                </div>
              </>
            ) : null}

            {(status || error) && (
              <div className="card-feedback" role="status" aria-live="polite">
                {status ? <p className="status-line">{status}</p> : null}
                {error ? <p className="error-line">{error}</p> : null}
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
