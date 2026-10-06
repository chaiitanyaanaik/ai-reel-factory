import { useState } from "react";
import { SignInButton, SignUpButton } from "@clerk/react";
import { login, type AuthUser } from "./api";
import { isClerkConfigured } from "./clerkConfig";

type Props = {
  onSignedIn: (user: AuthUser) => void;
};

const CREATORS = [
  {
    name: "Madison K.",
    handle: "@lifestyle_madi",
    badge: "Same-day batch",
    cover: "/landing/madi-cover.jpg",
    avatar: "/landing/madi-avatar.jpg",
    quote: "Used to take hours in Premiere. ReelKut spits out cutaways while I still have the take open.",
  },
  {
    name: "Dan Vance",
    handle: "@dan_strategy",
    badge: "Auto-paced",
    cover: "/landing/dan-cover.jpg",
    avatar: "/landing/dan-avatar.jpg",
    quote: "The cuts remove the awkward pauses. Retention feels tighter without rewriting a word.",
  },
  {
    name: "Chloe Chen",
    handle: "@chloebuilds",
    badge: "Sunday batch",
    cover: "/landing/chloe-cover.jpg",
    avatar: "/landing/chloe-avatar.jpg",
    quote: "I batch ten talking-head clips on Sunday. Fully exported before lunch.",
  },
  {
    name: "Marcus Rivera",
    handle: "@fitwithmarcus",
    badge: "Safe-zone",
    cover: "/landing/marcus-cover.jpg",
    avatar: "/landing/marcus-avatar.jpg",
    quote: "Captions never sit under the like button again. Safe-zone export is the quiet win.",
  },
];

const PROOF_AVATARS = [
  "/landing/proof-1.jpg",
  "/landing/proof-2.jpg",
  "/landing/proof-3.jpg",
  "/landing/madi-avatar.jpg",
];

const STEPS = [
  {
    n: "01",
    t: "Multi-take ingest",
    d: "Drop messy phone clips. Sequence them into one talking-head track — no manual trim hunt.",
    tip: "Camera roll → one timeline",
  },
  {
    n: "02",
    t: "Verbatim lip-sync",
    d: "Whisper pulls what you said. The plan never rewrites dialogue, so mouths stay honest.",
    tip: "0 words rewritten",
  },
  {
    n: "03",
    t: "Retention B-roll",
    d: "Cutaways land on spoken lines — face on the hook and the close, proof in the middle.",
    tip: "Pattern interrupts on the line",
  },
  {
    n: "04",
    t: "Safe-zone export",
    d: "9:16 render with room for IG and TikTok UI. Captions stay clear of likes and comments.",
    tip: "Reels · TikTok · Shorts",
  },
];

function scrollTo(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function InstagramPhone() {
  return (
    <div className="ig-stage" aria-hidden>
      <div className="ig-sticker ig-sticker-a">
        <span className="ig-sticker-label">Safe-zone</span>
        <strong>9:16 ready</strong>
      </div>
      <div className="ig-sticker ig-sticker-b">
        <strong>Same words</strong>
        <span className="ig-sticker-label">0 rewrites</span>
      </div>

      <div className="ig-phone">
        <div className="ig-notch" />
        <div className="ig-screen">
          <div className="ig-reel">
            <img className="ig-reel-photo" src="/landing/phone.jpg" alt="" />
            <div className="ig-reel-shade" />
            <div className="ig-side">
              <div className="ig-avatar-ring">
                <img className="ig-avatar" src="/landing/madi-avatar.jpg" alt="" />
              </div>
              <button type="button" className="ig-action" tabIndex={-1}>
                <span className="ig-heart" />
                <em>12.4k</em>
              </button>
              <button type="button" className="ig-action" tabIndex={-1}>
                <span className="ig-bubble" />
                <em>842</em>
              </button>
              <button type="button" className="ig-action" tabIndex={-1}>
                <span className="ig-send" />
                <em>Share</em>
              </button>
              <div className="ig-audio">
                <div className="ig-audio-disc" />
              </div>
            </div>
            <div className="ig-caption-card">
              <span className="ig-caption-badge">Auto-paced</span>
              <p>“Stop wasting hours in Premiere.”</p>
            </div>
            <div className="ig-tabbar">
              <span />
              <span />
              <span className="ig-tab-create" />
              <span />
              <span className="ig-tab-me" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Landing({ onSignedIn }: Props) {
  const clerk = isClerkConfigured();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const user = await login(email.trim(), name.trim() || undefined);
      onSignedIn(user);
    } catch (err) {
      setError(String((err as Error).message || err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="landing">
      <div className="landing-top">
        <div className="landing-grain landing-grain-on-dark" aria-hidden />

        <header className="landing-nav landing-nav-on-dark">
          <div className="landing-brand">
            <span className="landing-mark" aria-hidden>
              ✦
            </span>
            ReelKut
            <span className="landing-studio-pill">Studio</span>
          </div>
          <nav className="landing-nav-links" aria-label="Page">
            <button type="button" onClick={() => scrollTo("creators")}>
              Creators
            </button>
            <button type="button" onClick={() => scrollTo("how")}>
              How it works
            </button>
          </nav>
          <div className="landing-nav-actions">
            {clerk ? (
              <>
                <SignInButton mode="modal">
                  <button type="button" className="landing-link-btn">
                    Sign in
                  </button>
                </SignInButton>
                <SignUpButton mode="modal">
                  <button type="button" className="btn landing-nav-cta">
                    Start cutting →
                  </button>
                </SignUpButton>
              </>
            ) : (
              <>
                <button type="button" className="landing-link-btn" onClick={() => scrollTo("enter")}>
                  Sign in
                </button>
                <button type="button" className="btn landing-nav-cta" onClick={() => scrollTo("enter")}>
                  Start cutting →
                </button>
              </>
            )}
          </div>
        </header>

        <section className="landing-hero" id="enter">
          <div className="landing-hero-copy">
            <h1 className="landing-hero-line">
              Turn raw takes into{" "}
              <span className="landing-highlight">paced reels</span>
            </h1>
            <p className="landing-tagline">
              Upload a face-to-camera clip. Get tighter cuts and B-roll on the lines you said, ready
              to share.
            </p>

            {clerk ? (
              <div className="landing-form landing-form-clerk">
                <SignUpButton mode="modal">
                  <button type="button" className="btn landing-cta landing-cta-cream">
                    Start cutting →
                  </button>
                </SignUpButton>
                <p className="landing-trust" style={{ marginTop: "0.75rem" }}>
                  Already have an account?{" "}
                  <SignInButton mode="modal">
                    <button type="button" className="landing-link-btn" style={{ display: "inline" }}>
                      Sign in
                    </button>
                  </SignInButton>
                </p>
                <div className="landing-proof-row">
                  <div className="landing-proof-avatars">
                    {PROOF_AVATARS.map((src) => (
                      <img key={src} src={src} alt="" />
                    ))}
                  </div>
                  <p className="landing-trust">No credit card · Your cuts stay in your workspace</p>
                </div>
              </div>
            ) : (
              <form className="landing-form" onSubmit={onSubmit}>
                <label className="landing-field">
                  <span>Email</span>
                  <input
                    type="email"
                    required
                    autoComplete="email"
                    placeholder="you@studio.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    autoFocus
                  />
                </label>
                <label className="landing-field">
                  <span>Display name (optional)</span>
                  <input
                    type="text"
                    placeholder="optional"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    autoComplete="name"
                  />
                </label>
                <button
                  className="btn landing-cta landing-cta-cream"
                  type="submit"
                  disabled={busy || !email.trim()}
                >
                  {busy ? "Signing in…" : "Start cutting →"}
                </button>
                {error ? <p className="landing-error">{error}</p> : null}
                <div className="landing-proof-row">
                  <div className="landing-proof-avatars">
                    {PROOF_AVATARS.map((src) => (
                      <img key={src} src={src} alt="" />
                    ))}
                  </div>
                  <p className="landing-trust">No credit card · Your cuts stay in your workspace</p>
                </div>
              </form>
            )}
          </div>

          <InstagramPhone />
        </section>

        <div className="landing-ribbon">
          <span className="landing-ribbon-label">Built for</span>
          <div className="landing-ribbon-pills">
            <span>Founders</span>
            <span>Coaches</span>
            <span>Educators</span>
            <span>Course creators</span>
          </div>
        </div>

        <div className="landing-hero-slash" aria-hidden />
      </div>

      <section className="landing-creators" id="creators">
        <div className="landing-creators-head">
          <h2 className="landing-h2 landing-h2-wide">See what creators are making</h2>
          <p className="landing-lead-left">
            Solo founders and personal brands who publish talking-head — without a freelance edit
            queue.
          </p>
        </div>
        <div className="landing-creator-grid">
          {CREATORS.map((c) => (
            <article className="landing-creator-card" key={c.handle}>
              <div className="landing-creator-frame">
                <img src={c.cover} alt="" />
                <span className="landing-creator-chip">{c.badge}</span>
              </div>
              <div className="landing-creator-who">
                <img className="landing-creator-avatar" src={c.avatar} alt="" />
                <div>
                  <strong>{c.name}</strong>
                  <span>{c.handle}</span>
                </div>
              </div>
              <p className="landing-creator-quote">“{c.quote}”</p>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-verbatim" id="safe">
        <div className="landing-verbatim-ring" aria-hidden>
          <strong>0%</strong>
          <span>rewrites</span>
        </div>
        <div className="landing-verbatim-copy">
          <p className="landing-eyebrow">100% your voice</p>
          <h2 className="landing-h2 landing-h2-wide">
            Zero robotic voiceovers. Zero fake scripts. Always.
          </h2>
          <p>
            Most AI editors invent lines or swap in a clone. ReelKut cuts around what you actually
            said — same lips, same words, paced cutaways on the line.
          </p>
        </div>
      </section>

      <section className="landing-section landing-section-cream" id="how">
        <p className="landing-eyebrow">The creator pipeline</p>
        <h2 className="landing-h2">Not just another video editor</h2>
        <div className="landing-steps">
          {STEPS.map((s) => (
            <div className="landing-step" key={s.n}>
              <span className="landing-step-num">{s.n}</span>
              <h3>{s.t}</h3>
              <p>{s.d}</p>
              <span className="landing-step-tip">{s.tip}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="landing-quote">
        <blockquote>
          I record raw takes on my phone. ReelKut turns them into a paced reel without rewriting a
          word — it saves me hours every week.
        </blockquote>
        <div className="landing-quote-by">
          <div className="landing-quote-avatar" aria-hidden>
            RC
          </div>
          <div>
            <strong>Creator workflow</strong>
            <span>Talking-head → cutaways → publish</span>
          </div>
        </div>
      </section>

      <footer className="landing-footer">
        <div className="landing-footer-brand">
          <div className="landing-brand">
            <span className="landing-mark" aria-hidden>
              ✦
            </span>
            ReelKut
          </div>
          <p>Hyper-fast 9:16 cuts for solo creators. Your voice stays yours.</p>
        </div>
        <div className="landing-footer-links">
          <button type="button" onClick={() => scrollTo("how")}>
            How it works
          </button>
          <button type="button" onClick={() => scrollTo("creators")}>
            Creators
          </button>
          <button type="button" onClick={() => scrollTo("enter")}>
            Sign in
          </button>
        </div>
        <p className="landing-footer-note">Safe-zone verified · Founder-creators &amp; educators</p>
      </footer>
    </div>
  );
}
