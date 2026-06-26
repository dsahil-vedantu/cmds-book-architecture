import { useState } from 'react';
import { useGoogleLogin, type TokenResponse } from '@react-oauth/google';
import { useLocation, useNavigate } from 'react-router-dom';

import { Icon, GoogleG } from '../components/Icon';
import { Logo } from '../components/Logo';
import { useAuth } from '../auth/AuthProvider';
import { decodeGoogleIdToken, isAllowedDomain } from '../auth/google';

const ALLOWED_DOMAIN = import.meta.env.VITE_ALLOWED_DOMAIN ?? 'vedantu.com';
const HAS_CLIENT_ID = Boolean(import.meta.env.VITE_GOOGLE_CLIENT_ID);

type FloatCardProps = {
  icon: 'upload' | 'sparkles' | 'download';
  title: string;
  sub: string;
  progress?: boolean;
  done?: boolean;
  delay?: number;
};

function FloatCard({ icon, title, sub, progress, done, delay = 0 }: FloatCardProps) {
  return (
    <div
      className="fade-up"
      style={{
        background: 'rgba(255,255,255,0.10)',
        backdropFilter: 'blur(10px)',
        border: '1px solid rgba(255,255,255,0.18)',
        borderRadius: 14,
        padding: '12px 14px',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        minWidth: 280,
        animationDelay: `${delay}ms`,
      }}
    >
      <div
        style={{
          width: 36,
          height: 36,
          borderRadius: 10,
          background: done ? 'rgba(16,185,129,0.25)' : 'rgba(255,255,255,0.16)',
          display: 'grid',
          placeItems: 'center',
          color: '#fff',
        }}
      >
        <Icon name={icon} size={18} />
      </div>
      <div style={{ flex: 1, color: '#fff', lineHeight: 1.3 }}>
        <div style={{ fontSize: 13.5, fontWeight: 600 }}>{title}</div>
        <div style={{ fontSize: 11.5, opacity: 0.75, marginTop: 2 }}>{sub}</div>
        {progress && (
          <div
            style={{
              height: 4,
              marginTop: 8,
              background: 'rgba(255,255,255,0.18)',
              borderRadius: 4,
            }}
          >
            <div
              style={{
                width: '65%',
                height: '100%',
                background: 'linear-gradient(90deg, #fff, #FFC7BB)',
                borderRadius: 4,
              }}
            />
          </div>
        )}
      </div>
      {done && (
        <div style={{ color: '#A7F3D0' }}>
          <Icon name="check" size={18} />
        </div>
      )}
    </div>
  );
}

export default function LoginPage() {
  const { signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from =
    (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? '/library';

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Always call the hook (rules-of-hooks); we just don't invoke the returned
  // function in dev-stub mode.
  const startGoogleLogin = useGoogleLogin({
    flow: 'implicit',
    onSuccess: async (resp: TokenResponse) => {
      setBusy(true);
      setError(null);
      try {
        const res = await fetch('https://www.googleapis.com/oauth2/v3/userinfo', {
          headers: { Authorization: `Bearer ${resp.access_token}` },
        });
        if (!res.ok) throw new Error('Failed to fetch Google profile');
        const profile: { email: string; name: string; picture?: string; hd?: string } =
          await res.json();

        if (!isAllowedDomain(profile.email, ALLOWED_DOMAIN)) {
          setError(
            `Access restricted to @${ALLOWED_DOMAIN}. Signed-in account: ${profile.email}.`
          );
          setBusy(false);
          return;
        }
        signIn({
          email: profile.email,
          name: profile.name,
          picture: profile.picture,
          initials: '',
        });
        navigate(from, { replace: true });
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Sign-in failed');
        setBusy(false);
      }
    },
    onError: () => {
      setBusy(false);
      setError('Google sign-in was cancelled or failed.');
    },
  });

  // Local-dev shim: if there's no Google client ID configured, fall back to
  // an inline "Continue as dev user" sign-in so the rest of the UI is
  // demoable end-to-end without OAuth setup. Real client ID is required
  // before this ships to ops.
  const handleDevLogin = () => {
    setBusy(true);
    setTimeout(() => {
      signIn({
        email: `dev@${ALLOWED_DOMAIN}`,
        name: 'Dev User',
        picture: undefined,
        initials: '',
      });
      navigate(from, { replace: true });
    }, 600);
  };

  const handleClick = () => {
    setError(null);
    if (HAS_CLIENT_ID) startGoogleLogin();
    else handleDevLogin();
  };

  // Decoder is exported for the (future) ID-token flow when the backend
  // /auth/google endpoint lands. Referencing it here also keeps the import
  // alive so tree-shakers don't drop it during the Phase 5 wiring.
  void decodeGoogleIdToken;

  return (
    <div className="login-shell">
      <div className="login-art">
        <div style={{ position: 'relative' }}>
          <Logo size="lg" />
        </div>

        <div style={{ marginTop: 80, maxWidth: 520, position: 'relative' }}>
          <div
            style={{
              fontSize: 13,
              fontWeight: 600,
              letterSpacing: '0.14em',
              textTransform: 'uppercase',
              opacity: 0.7,
              marginBottom: 18,
            }}
          >
            Content Studio · v2
          </div>
          <h1
            style={{
              fontSize: 48,
              lineHeight: 1.05,
              fontWeight: 800,
              letterSpacing: '-0.025em',
              margin: 0,
            }}
          >
            Turn textbook PDFs into{' '}
            <span style={{ color: '#FFC7BB' }}>publish-ready chapters</span> in minutes.
          </h1>
          <p style={{ fontSize: 16, lineHeight: 1.55, opacity: 0.82, marginTop: 22 }}>
            Drop a PDF. V-Studio analyses, extracts, regenerates and lays it out — chapter by
            chapter, theory, questions and figures, all in one place.
          </p>
        </div>

        <div
          style={{
            position: 'absolute',
            right: 56,
            bottom: 56,
            display: 'flex',
            flexDirection: 'column',
            gap: 14,
            alignItems: 'flex-end',
          }}
        >
          <FloatCard icon="upload"   title="Upload"                 sub="218 pages · 12.4 MB"             delay={0} />
          <FloatCard icon="sparkles" title="Regenerating ch 3/12"    sub="65% — theory, questions, figures" delay={120} progress />
          <FloatCard icon="download" title="DOCX ready"              sub="Quadratic Equations · 8 chapters"  delay={240} done />
        </div>

        <div style={{ position: 'absolute', bottom: 24, left: 56, fontSize: 12, opacity: 0.55 }}>
          © 2026 Vedantu Innovations · Internal tool
        </div>
      </div>

      <div className="login-form">
        <div style={{ width: '100%', maxWidth: 380 }}>
          <div
            style={{
              fontSize: 13,
              color: 'var(--ink-500)',
              fontWeight: 600,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
            }}
          >
            Welcome back
          </div>
          <h2
            style={{
              fontSize: 30,
              fontWeight: 800,
              letterSpacing: '-0.02em',
              margin: '8px 0 8px',
              color: 'var(--ink-900)',
            }}
          >
            Sign in to V-Studio
          </h2>
          <p style={{ color: 'var(--ink-500)', fontSize: 14, margin: 0 }}>
            Use your <strong style={{ color: 'var(--ink-800)' }}>@{ALLOWED_DOMAIN}</strong>{' '}
            Google account. Restricted to the content ops team.
          </p>

          <div style={{ marginTop: 28 }}>
            <button className="gbtn" onClick={handleClick} disabled={busy}>
              {busy ? <span className="spinner dark" /> : <GoogleG size={18} />}
              <span>{busy ? 'Signing you in…' : 'Continue with Google'}</span>
            </button>
          </div>

          {error && (
            <div
              style={{
                marginTop: 14,
                padding: '10px 14px',
                background: 'var(--red-50)',
                border: '1px solid var(--red-100)',
                borderRadius: 10,
                color: 'var(--red-700)',
                fontSize: 13,
              }}
            >
              {error}
            </div>
          )}

          {!HAS_CLIENT_ID && (
            <div
              style={{
                marginTop: 14,
                padding: '10px 14px',
                background: 'var(--warning-bg)',
                border: '1px solid #F5E2BD',
                borderRadius: 10,
                color: '#8A5300',
                fontSize: 12.5,
                lineHeight: 1.5,
              }}
            >
              <strong>Dev mode.</strong> No <code>VITE_GOOGLE_CLIENT_ID</code> set —
              "Continue with Google" signs you in as a stub dev user. Configure the
              client ID in <code>.env.local</code> before sharing with ops.
            </div>
          )}

          <div
            style={{
              marginTop: 28,
              padding: '14px 16px',
              background: 'var(--indigo-50)',
              border: '1px solid var(--indigo-100)',
              borderRadius: 12,
              display: 'flex',
              gap: 12,
            }}
          >
            <Icon name="shield" size={18} style={{ color: 'var(--indigo-700)' }} />
            <div style={{ fontSize: 12.5, color: 'var(--ink-700)', lineHeight: 1.5 }}>
              Single sign-on enforced. Your activity is audited per Vedantu's internal-tool
              policy.
            </div>
          </div>

          <div style={{ marginTop: 28, fontSize: 12, color: 'var(--ink-500)' }}>
            Trouble signing in?{' '}
            <a
              href="#"
              style={{ color: 'var(--indigo-700)', fontWeight: 600, textDecoration: 'none' }}
            >
              Ping #ops-tools
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
