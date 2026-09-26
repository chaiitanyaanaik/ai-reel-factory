import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth, useClerk, useUser, UserButton } from "@clerk/react";
import {
  artifactUrl,
  clearSession,
  clipUrl,
  createProject,
  fetchMe,
  getProject,
  getScript,
  getStoredToken,
  getStoredUser,
  listProjects,
  logout,
  pollJobUntilDone,
  reorderClips,
  setMemoryToken,
  setSession,
  setTokenProvider,
  startJob,
  uploadClips,
  type AuthUser,
  type ClipInfo,
  type ProjectDetail,
  type ProjectSummary,
} from "./api";
import { isClerkConfigured } from "./clerkConfig";
import Landing from "./Landing";

type View = "library" | "wizard";
type Step = "upload" | "script" | "broll" | "final";

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
      } catch {
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
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  useEffect(() => {
    if (bootError) setError(bootError);
  }, [bootError]);

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
    if (previewUrl?.startsWith("blob:")) URL.revokeObjectURL(previewUrl);
    if (pendingFiles[0]) {
      setPreviewUrl(URL.createObjectURL(pendingFiles[0]));
      return;
    }
    if (clips[0] && projectId) {
      setPreviewUrl(clipUrl(projectId, clips[0].filename));
      return;
    }
    if (projectId && detail?.artifacts.reel) {
      setPreviewUrl(artifactUrl(projectId, "reel"));
      return;
    }
    setPreviewUrl(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingFiles, clips, projectId, detail?.artifacts.reel]);

  const doneFlags = useMemo(
    () => ({
      upload: completed.upload || clips.length > 0,
      script: completed.script || Boolean(detail?.artifacts.script),
      broll: Boolean(completed.broll),
      final: completed.final || Boolean(detail?.artifacts.reel),
    }),
    [completed, clips.length, detail]
  );

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
    try {
      const d = await refreshProject(id);
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
        const skips = (d.run_report?.broll ?? []).filter(
          (b) => b.source === "skipped_keep_aroll"
        );
        const veoUsed = d.run_report?.veo_clips_used ?? 0;
        if (skips.length && veoUsed === 0) {
          setError(
            "B-roll finished with no clips (Veo skipped). Check quota/billing, then retry Generate."
          );
        }
      }
      if (to === "render" || to === "cover") setCompleted((c) => ({ ...c, final: true }));
      setStep(next);
      setStatus(`${label} complete.`);
      await refreshLibrary();
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy(false);
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
        {view === "library" ? (
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
                    <div className="phone">
                      {previewUrl ? (
                        <video key={previewUrl} src={previewUrl} muted playsInline preload="metadata" />
                      ) : (
                        <div className="phone-empty">Preview appears after you add a clip</div>
                      )}
                    </div>
                    <p className="preview-time">9:16 preview</p>
                  </aside>                </div>

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
                    Merge → enhance → transcribe → editor plan. Same spoken words — lip-sync safe.
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
                <div className="single-pane">
                  <h2 className="page-title">Generate B-roll</h2>
                  <p className="page-sub">
                    Align the edit plan and generate cutaways with Veo (Lite). Max ~5, face on hook &amp; ending.
                  </p>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={busy || !doneFlags.script}
                    onClick={() => runStage("align", "broll", "final", "Generating B-roll")}
                  >
                    Generate B-roll
                  </button>
                </div>
                <div className="footer-bar">
                  <button type="button" className="btn-text" onClick={() => setStep("script")}>
                    ← Back
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={!doneFlags.broll || busy}
                    onClick={() => setStep("final")}
                  >
                    Continue: Final export →
                  </button>
                </div>
              </>
            ) : null}

            {step === "final" ? (
              <>
                <div className="workspace">
                  <div className="workspace-main">
                    <h2 className="page-title">Final export</h2>
                    <p className="page-sub">Render the vertical reel, then optionally generate a cover.</p>
                    <div className="form-row">
                      <button
                        type="button"
                        className="btn btn-primary"
                        disabled={busy}
                        onClick={() => runStage("subtitles", "render", "final", "Rendering reel")}
                      >
                        Render reel
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
                        <a className="btn btn-ghost" href={artifactUrl(projectId, "reel")} download>
                          Download reel
                        </a>
                        {detail.artifacts.cover ? (
                          <a className="btn btn-ghost" href={artifactUrl(projectId, "cover")} download>
                            Download cover
                          </a>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                  <aside className="workspace-side">
                    <div className="phone">
                      {projectId && detail?.artifacts.reel ? (
                        <video
                          key={artifactUrl(projectId, "reel")}
                          src={artifactUrl(projectId, "reel")}
                          controls
                          playsInline
                        />
                      ) : (
                        <div className="phone-empty">Render to preview your reel</div>
                      )}
                    </div>
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
