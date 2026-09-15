"use client";

import {
  CheckCircle2,
  Facebook,
  Instagram,
  Mail,
  RotateCcw,
  SearchCheck,
  ShieldCheck,
  UserRoundPlus,
  UsersRound,
  Youtube,
} from "lucide-react";
import Image from "next/image";

/**
 * The Social Scout auth shell — hero on the left, glass card on the right.
 *
 * Ported from the `social-scout-login` handoff. Every visual is CSS: the
 * ListenFirst mark, the orbit rings, the profile card and the social bubbles
 * are shapes and gradients, so there are no image assets to ship. Styles live
 * in `auth-design.css`, namespaced under `.ss-auth` because the handoff's class
 * names (.page, .hero, .feature, .verified) would otherwise collide with the
 * rest of the app — CSS in the app router is global whichever page imports it.
 *
 * `children` is the form body, so sign-in, reset and change-password all sit in
 * the same card and cannot drift apart.
 */
export function AuthLayout({
  heading,
  subtitle,
  children,
}: {
  heading: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <main className="ss-auth">
      <div className="ambient-layer" aria-hidden>
        <div className="ambient ambient-one" />
        <div className="ambient ambient-two" />
        <div className="ambient ambient-three" />
      </div>

      <section className="hero">
        <header className="brand">
          <BrandMark />
          <span>LISTENFIRST</span>
        </header>

        <div className="hero-copy">
          <div className="eyebrow">
            <span>✦</span> SOCIAL INTELLIGENCE
          </div>

          <h1>
            Social <em>Scout</em>
          </h1>

          <div className="headline-line" />

          <p className="tagline">
            Where talent handles get found, verified,
            <br />
            and signed off.
          </p>

          <div className="features">
            <Feature
              icon={<SearchCheck size={24} />}
              tone="blue"
              title="Finds every handle"
              text="Instagram, Facebook, YouTube, TikTok and X — from a name, a Wikipedia page, or a single handle you already have."
            />
            <Feature
              icon={<ShieldCheck size={24} />}
              tone="cyan"
              title="Verifies before it claims"
              text="Each link is checked against the subject's own identity record. Anything short of certain comes back as Manual Review, never a guess."
            />
            <Feature
              icon={<UsersRound size={24} />}
              tone="purple"
              title="Remembers your decisions"
              text="Approve or reject once and the next run reuses it — cheaper every time, and consistent across the team."
            />
          </div>

          <div className="platform-row">
            <span className="platform">
              <Instagram size={16} /> Instagram
            </span>
            <span className="platform">
              <Facebook size={16} fill="currentColor" /> Facebook
            </span>
            <span className="platform">
              <Youtube size={16} fill="currentColor" /> YouTube
            </span>
            <span className="platform">
              <span className="tiktok">♪</span> TikTok
            </span>
            <span className="platform">
              <b>𝕏</b> X
            </span>
          </div>
        </div>

        <VerificationVisual />

        <footer className="hero-footer">
          <span>
            <ShieldCheck size={18} /> Authorized access only
          </span>
          <span className="footer-divider" />
          <span>
            <Mail size={18} /> support@listenfirstmedia.com
          </span>
        </footer>
      </section>

      <section className="auth-side">
        <div className="auth-card">
          <div className="auth-logo">
            <BrandMark small />
          </div>

          <h2>{heading}</h2>
          <p className="auth-subtitle">{subtitle}</p>

          {children}

          <div className="or">
            <span>OR</span>
          </div>

          <div className="help-list">
            <HelpItem
              icon={<UserRoundPlus size={23} />}
              title="No account?"
              text="Accounts are created by an administrator — there is no self sign-up."
            />
            <HelpItem
              icon={<RotateCcw size={23} />}
              title="Locked out?"
              text="Use Forgot to email yourself a code, or ask an admin for a new temporary password."
            />
          </div>

          <div className="bottom-glow" />
        </div>
      </section>
    </main>
  );
}

/**
 * The real ListenFirst mark.
 *
 * The handoff approximated it with three clipped <span>s because it had no
 * asset to work from. We do, so the actual logo goes in — the CSS keeps the
 * circular frame and the yellow glow around it.
 */
function BrandMark({ small }: { small?: boolean }) {
  return (
    <div className={`brand-mark${small ? " small" : ""}`}>
      <Image
        src="/listenfirst-mark.jpg"
        alt=""
        width={48}
        height={48}
        priority
      />
    </div>
  );
}

function Feature({
  icon,
  tone,
  title,
  text,
}: {
  icon: React.ReactNode;
  tone: "blue" | "cyan" | "purple";
  title: string;
  text: string;
}) {
  return (
    <div className="feature">
      <div className={`feature-icon ${tone}`}>{icon}</div>
      <div>
        <h3>{title}</h3>
        <p>{text}</p>
      </div>
    </div>
  );
}

function HelpItem({
  icon,
  title,
  text,
}: {
  icon: React.ReactNode;
  title: string;
  text: string;
}) {
  return (
    <div className="help-item">
      <div className="help-icon">{icon}</div>
      <p>
        <strong>{title}</strong> {text}
      </p>
    </div>
  );
}

/** The orbiting profile card. Decorative, so hidden from assistive tech. */
function VerificationVisual() {
  return (
    <div className="verification" aria-hidden>
      <div className="orbit orbit-a" />
      <div className="orbit orbit-b" />
      <div className="orbit orbit-c" />

      <div className="social-bubble instagram-b">
        <Instagram size={31} />
      </div>
      <div className="social-bubble youtube-b">
        <Youtube size={31} fill="currentColor" />
      </div>
      <div className="social-bubble facebook-b">
        <Facebook size={32} fill="currentColor" />
      </div>
      <div className="social-bubble x-b">𝕏</div>
      <div className="social-bubble tiktok-b">♪</div>

      <div className="profile-card">
        <div className="profile-avatar">
          <UsersRound size={39} />
        </div>
        <div className="profile-lines">
          <i />
          <i />
          <i />
          <i />
        </div>
        <div className="verified">
          <CheckCircle2 size={31} />
        </div>
      </div>

      <div className="dot-grid" />
    </div>
  );
}

/** Inline feedback. The mock has no error state; these reuse its palette. */
export function AuthMessage({
  tone,
  children,
}: {
  tone: "error" | "info";
  children: React.ReactNode;
}) {
  return (
    <div className={`auth-message ${tone}`} role={tone === "error" ? "alert" : "status"}>
      <span>{children}</span>
    </div>
  );
}
