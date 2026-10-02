import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth, useClerk, useUser, UserButton } from "@clerk/react";
import {
  clearSession,
  createProject,
  downloadAuthenticated,
  editBroll,
  fetchMe,
  fetchMediaObjectUrl,
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
  uploadClips,
  type AuthUser,
  type BrandProfile,
  type BrollClip,
  type ClipInfo,
  type ProjectDetail,
  type ProjectSummary,
} from "./api";
import { isClerkConfigured } from "./clerkConfig";
import Landing from "./Landing";
import PhoneVideo from "./PhoneVideo";

type View = "library" | "wizard" | "brand";
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

function guessDurationLabel(bytes: number) {
  // Placeholder until we probe media; keep UI calm
  if (bytes < 5_000_000) return "~0:15";
  if (bytes < 20_000_000) return "~0:40";
  if (bytes < 60_000_000) return "~1:20";
  return "~2:00+";
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
        const me = await fetchMe();
        if (cancelled) return;
        onReady(me || authUser);
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
  const [projectId, setProjectId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [step, setStep] = useState<Step>("upload");
  const [name, setName] = useState("");
  const [topic, setTopic] = useState("");
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
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
  const [selectedBrollIndex, setSelectedBrollIndex] = useState<number | null>(null);
  const [brollMediaUrls, setBrollMediaUrls] = useState<Record<number, string>>({});
  const [pendingPreviewUrl, setPendingPreviewUrl] = useState<string | null>(null);
  const [brandForm, setBrandForm] = useState<BrandProfile>(EMPTY_BRAND);
  const [brandLoaded, setBrandLoaded] = useState(false);
  const [brandBusy, setBrandBusy] = useState(false);

  useEffect(() => {
    if (bootError) setError(bootError);
  }, [bootError]);

  const loadBrand = useCallback(async () => {
    const b = await getBrand();
    setBrandForm({ ...EMPTY_BRAND, ...b });
    setBrandLoaded(true);
  }, []);

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

  const refreshLibrary = useCallback(async () => {
    setProjects(await listProjects());
  }, []);

  const refreshProject = useCallback(async (id: string) => {
    const d = await getProject(id);
    setDetail(d);
    setClips(d.clips ?? []);
    if (d.artifacts.script) {
      try {
        setScript(await getScript(id));
      } catch {
        /* ignore */
      }
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

  useEffect(() => {
    // Always hydrate from disk when visiting this step (run_report may omit broll stage).
    if (step === "broll" && projectId) {
      void loadBrollClips(projectId);
    }
  }, [step, projectId, loadBrollClips]);

  useEffect(() => {
    if (!projectId || brollClips.length === 0) {
      setBrollMediaUrls((prev) => {
        for (const u of Object.values(prev)) {
          if (u.startsWith("blob:")) URL.revokeObjectURL(u);
        }
        return {};
      });
      return;
    }
    let cancelled = false;
    const created: string[] = [];
    (async () => {
      const entries = await Promise.all(
        brollClips.map(async (c) => {
          const idx = Number(c.broll_index);
          if (!c.video_url && !c.path) return [idx, ""] as const;
          try {
            const url = await fetchMediaObjectUrl(
              `/projects/${projectId}/broll/${idx}/video`
            );
            created.push(url);
            return [idx, url] as const;
          } catch (e) {
            console.warn("B-roll media load failed", idx, e);
            return [idx, ""] as const;
          }
        })
      );
      if (cancelled) {
        for (const u of created) URL.revokeObjectURL(u);
        return;
      }
      setBrollMediaUrls((prev) => {
        for (const u of Object.values(prev)) {
          if (u.startsWith("blob:")) URL.revokeObjectURL(u);
        }
        const map: Record<number, string> = {};
        for (const [idx, url] of entries) {
          if (url) map[idx] = url;
        }
        return map;
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, brollClips]);

  useEffect(() => {
    if (menuOpenIndex === null) return;
    const onDoc = () => setMenuOpenIndex(null);
    document.addEventListener("click", onDoc);
    return () => document.removeEventListener("click", onDoc);
  }, [menuOpenIndex]);

  async function onNewCut() {
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
      setStep("upload");
      setView("wizard");
      await refreshProject(created.id);
      await refreshLibrary();
      setStatus("");
      setName("");
      setTopic("");
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy(false);
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

  function moveClip(i: number, dir: -1 | 1) {
    const j = i + dir;
    if (j < 0 || j >= clips.length) return;
    const next = [...clips];
    [next[i], next[j]] = [next[j], next[i]];
    setClips(next);
  }

  async function saveSequenceAndContinue() {
    if (!projectId) return;
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
              setError("");
              setStatus("");
              loadBrand().catch((e) => setError(String((e as Error).message || e)));
            }}
          >
            Brand
          </button>
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
              setStatus("");
              setError("");
            }}
          >
            + New Cut
          </button>
        </div>
      </header>

      <main className="main">
        {view === "brand" ? (
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
              <div className="form-row">
                <div className="field">
                  <label htmlFor="name">Name</label>
                  <input
                    id="name"
                    placeholder="optional"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </div>
                <div className="field">
                  <label htmlFor="topic">Intent</label>
                  <input
                    id="topic"
                    placeholder="B-roll theme only (not dialogue)"
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
                  />
                </div>
                <button className="btn btn-primary" type="button" disabled={busy} onClick={onNewCut}>
                  Create cut
                </button>
              </div>
              {status ? <p className="status-line">{status}</p> : null}
              {error ? <p className="error-line">{error}</p> : null}
            </div>
            <div className="library-grid">
              {!projects.length ? (
                <p className="muted">No projects yet.</p>
              ) : (
                projects.map((p) => (
                  <button key={p.id} type="button" className="project-card" onClick={() => openProject(p.id)}>
                    <div>
                      <h3>{p.id}</h3>
                      <p>
                        {p.clip_count} clip{p.clip_count === 1 ? "" : "s"}
                        {p.topic ? ` · ${p.topic}` : ""}
                        {p.updated_at ? ` · ${new Date(p.updated_at).toLocaleString()}` : ""}
                      </p>
                    </div>
                    <span className={`badge ${p.has_reel ? "ready" : ""}`}>
                      {p.has_reel ? "Ready" : p.has_script ? "Script" : p.status}
                    </span>
                  </button>
                ))
              )}
            </div>
          </div>
        ) : (
          <div className="card">
            <div className="stepper">
              {STEPS.map((s) => {
                const idx = STEPS.findIndex((x) => x.id === s.id);
                const current = STEPS.findIndex((x) => x.id === step);
                const done = doneFlags[s.id] || idx < current;
                return (
                  <button
                    key={s.id}
                    type="button"
                    className={`step ${step === s.id ? "active" : ""} ${done && step !== s.id ? "done" : ""}`}
                    onClick={() => setStep(s.id)}
                  >
                    <span className="num">{s.num}</span>
                    <span className="step-label">{s.label}</span>
                  </button>
                );
              })}
            </div>

            {step === "upload" ? (
              <>
                <div className="workspace">
                  <div className="workspace-main">
                    <h2 className="page-title">Sequence your clips</h2>
                    <p className="page-sub">Arrange in the order you want them merged.</p>

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
                              {guessDurationLabel(f.size)} · {formatBytes(f.size)} · pending
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
                              {guessDurationLabel(c.size_bytes)} · {formatBytes(c.size_bytes)}
                            </div>
                          </div>
                          <div className="icon-btns">
                            <button type="button" className="icon-btn" onClick={() => moveClip(i, -1)} disabled={i === 0}>
                              ↑
                            </button>
                            <button
                              type="button"
                              className="icon-btn"
                              onClick={() => moveClip(i, 1)}
                              disabled={i === clips.length - 1}
                            >
                              ↓
                            </button>
                          </div>
                        </li>
                      ))}
                    </ul>

                    <label className="add-drop">
                      <input
                        type="file"
                        accept="video/mp4,video/quicktime,.mp4,.mov"
                        multiple
                        hidden
                        onChange={(e) => {
                          if (e.target.files?.length) {
                            setPendingFiles([...pendingFiles, ...Array.from(e.target.files)]);
                          }
                          e.target.value = "";
                        }}
                      />
                      <strong>{pendingFiles.length || clips.length ? "+ Add another clip" : "+ Add clip"}</strong>
                      <span>MP4 or MOV · or Record in studio (Coming soon)</span>
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
                  <button type="button" className="btn-text" onClick={() => setView("library")}>
                    Save draft
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={busy || (!pendingFiles.length && !clips.length)}
                    onClick={saveSequenceAndContinue}
                  >
                    Continue: Extract script →
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
                  <button type="button" className="btn-text" onClick={() => setStep("upload")}>
                    ← Back
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={!doneFlags.script || busy}
                    onClick={() => setStep("broll")}
                  >
                    Continue: Generate B-roll →
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
                      ready, review each one — open the ⋮ menu and choose Edit if you want to change a
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
                                {brollMediaUrls[idx] ? (
                                  <video
                                    key={`${idx}-${clip.versions?.length ?? 0}-${brollMediaUrls[idx]}`}
                                    src={brollMediaUrls[idx]}
                                    muted
                                    playsInline
                                    preload="metadata"
                                  />
                                ) : (
                                  <div className="broll-thumb-ph">
                                    {hasVideo ? "…" : "Skipped"}
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
                      srcPath={
                        projectId &&
                        selectedBrollIndex != null &&
                        (brollMediaUrls[selectedBrollIndex] ||
                          brollClips.some(
                            (c) =>
                              Number(c.broll_index) === selectedBrollIndex &&
                              (c.video_url || c.path)
                          ))
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
                  <button type="button" className="btn-text" onClick={() => setStep("script")}>
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
                      onClick={() => setStep("final")}
                    >
                      Continue: Final export →
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
                    <div className="form-row">
                      <button
                        type="button"
                        className="btn btn-primary"
                        disabled={busy}
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
                    </div>
                    {projectId && detail?.artifacts.reel ? (
                      <div className="form-row" style={{ marginTop: "1rem" }}>
                        <button
                          type="button"
                          className="btn btn-ghost"
                          disabled={busy}
                          onClick={() =>
                            void downloadAuthenticated(
                              `/projects/${projectId}/artifacts/reel`,
                              "reel.mp4"
                            ).catch((e) => setError(String((e as Error).message || e)))
                          }
                        >
                          Download reel
                        </button>
                        {detail.artifacts.cover ? (
                          <button
                            type="button"
                            className="btn btn-ghost"
                            disabled={busy}
                            onClick={() =>
                              void downloadAuthenticated(
                                `/projects/${projectId}/artifacts/cover`,
                                "cover.jpg"
                              ).catch((e) => setError(String((e as Error).message || e)))
                            }
                          >
                            Download cover
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                  <aside className="workspace-side">
                    <PhoneVideo
                      srcPath={
                        projectId && detail?.artifacts.reel
                          ? `/projects/${projectId}/artifacts/reel`
                          : null
                      }
                      cacheKey={reelPreviewKey}
                      emptyText="Render to preview your reel"
                    />
                  </aside>
                </div>
                <div className="footer-bar">
                  <button type="button" className="btn-text" onClick={() => setStep("broll")}>
                    ← Back
                  </button>
                  <button type="button" className="btn btn-ghost" onClick={() => setView("library")}>
                    Back to library
                  </button>
                </div>
              </>
            ) : null}

            {status ? <p className="status-line">{status}</p> : null}
            {error ? <p className="error-line">{error}</p> : null}
          </div>
        )}
      </main>
    </div>
  );
}
