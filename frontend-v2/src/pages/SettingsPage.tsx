import { useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';

import { GoogleG, Icon, type IconName } from '../components/Icon';
import { useAuth } from '../auth/AuthProvider';
import { TEMPLATES } from '../mocks/templates';

type Section = 'account' | 'keys' | 'defaults' | 'security';
const SECTIONS: Array<{ id: Section; label: string; icon: IconName }> = [
  { id: 'account',  label: 'Account',  icon: 'user' },
  { id: 'keys',     label: 'API Keys', icon: 'key' },
  { id: 'defaults', label: 'Defaults', icon: 'wand' },
  { id: 'security', label: 'Security', icon: 'shield' },
];

export default function SettingsPage() {
  const [section, setSection] = useState<Section>('account');
  const navigate = useNavigate();

  return (
    <div className="content fade-up">
      <div className="content-narrow">
        <div className="page-header">
          <div>
            <h1 className="page-title">Settings</h1>
            <div className="page-sub">Account, API access and defaults.</div>
          </div>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => navigate('/library')}
            title="Back to library"
          >
            <Icon name="arrow-l" size={14} /> Back to library
          </button>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 24 }}>
          <div className="col gap-2">
            {SECTIONS.map((s) => {
              const active = section === s.id;
              return (
                <button
                  key={s.id}
                  onClick={() => setSection(s.id)}
                  className="nav-item"
                  style={{
                    color: active ? 'var(--indigo-700)' : 'var(--ink-700)',
                    background: active ? 'var(--indigo-50)' : 'transparent',
                    border: active
                      ? '1px solid var(--indigo-100)'
                      : '1px solid transparent',
                  }}
                >
                  <Icon name={s.icon} size={16} className="ic" />
                  {s.label}
                </button>
              );
            })}
          </div>

          <div>
            {section === 'account' && <AccountSection />}
            {section === 'keys' && <KeysSection />}
            {section === 'defaults' && <DefaultsSection />}
            {section === 'security' && <SecuritySection />}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------- Shared bits ----------

function SettingsCard({
  title,
  sub,
  actions,
  children,
}: {
  title: string;
  sub?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="card" style={{ padding: 24, marginBottom: 16 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          marginBottom: 18,
        }}
      >
        <div>
          <div
            style={{
              fontSize: 15,
              fontWeight: 700,
              color: 'var(--ink-900)',
              letterSpacing: '-0.01em',
            }}
          >
            {title}
          </div>
          {sub && (
            <div style={{ fontSize: 12.5, color: 'var(--ink-500)', marginTop: 4 }}>{sub}</div>
          )}
        </div>
        {actions}
      </div>
      {children}
    </div>
  );
}

function ToggleRow({
  label,
  sub,
  on: initialOn,
}: {
  label: string;
  sub?: string;
  on?: boolean;
}) {
  const [on, setOn] = useState(Boolean(initialOn));
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 14,
        padding: '12px 0',
        borderTop: '1px solid var(--line-2)',
      }}
    >
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--ink-900)' }}>{label}</div>
        {sub && (
          <div style={{ fontSize: 11.5, color: 'var(--ink-500)', marginTop: 2 }}>{sub}</div>
        )}
      </div>
      <div className={`toggle ${on ? 'on' : ''}`} onClick={() => setOn((v) => !v)} />
    </div>
  );
}

// ---------- Account ----------

function AccountSection() {
  const { user } = useAuth();
  const name = user?.name ?? 'Priya Verma';
  const email = user?.email ?? 'priya.v@vedantu.com';
  const initials = user?.initials ?? 'PV';

  return (
    <>
      <SettingsCard title="Profile" sub="Visible to teammates in the audit log.">
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 18 }}>
          <div className="avatar" style={{ width: 56, height: 56, fontSize: 20 }}>
            {initials}
          </div>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink-900)' }}>
              {name}
            </div>
            <div style={{ fontSize: 13, color: 'var(--ink-500)' }}>
              {email} · Content Ops Lead
            </div>
          </div>
          <button className="btn btn-ghost btn-sm" style={{ marginLeft: 'auto' }}>
            Change photo
          </button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <div className="field">
            <label>Full name</label>
            <input type="text" defaultValue={name} />
          </div>
          <div className="field">
            <label>Email (managed via Google)</label>
            <input
              type="email"
              defaultValue={email}
              disabled
              style={{ background: 'var(--bg-tint)' }}
            />
          </div>
          <div className="field">
            <label>Team</label>
            <input type="text" defaultValue="Content Operations" />
          </div>
          <div className="field">
            <label>Timezone</label>
            <input type="text" defaultValue="Asia/Kolkata (IST)" />
          </div>
        </div>
      </SettingsCard>

      <SettingsCard title="Notifications" sub="When V-Studio pings you.">
        <ToggleRow
          label="Email me when a book finishes processing"
          sub={`Sent to ${email}`}
          on
        />
        <ToggleRow
          label="Slack #content-ops notification"
          sub="Per book completion"
          on
        />
        <ToggleRow label="Weekly summary digest" sub="Mondays at 9am IST" />
      </SettingsCard>
    </>
  );
}

// ---------- Keys ----------

function KeysSection() {
  return (
    <>
      <SettingsCard
        title="LLM provider"
        sub="V-Studio uses these for theory + question regeneration."
        actions={
          <span className="badge ok">
            <span className="dot" />
            Connected
          </span>
        }
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <KeyRow
            provider="Gemini 2.5 Pro"
            keyMasked="AIza••••••••••••••••••••MhT9"
            usage="48,210 / 100k req · this month"
          />
          <KeyRow
            provider="Claude Haiku 4.5"
            keyMasked="sk-ant-••••••••••••••••8a2c"
            usage="12,043 / 50k req · this month"
          />
          <KeyRow
            provider="OpenAI GPT-4o"
            keyMasked="sk-••••••••••••••••••••a1b2"
            usage="Unused — fallback only"
          />
        </div>
        <div style={{ marginTop: 16 }}>
          <button className="btn btn-soft btn-sm">
            <Icon name="plus" size={12} /> Add provider
          </button>
        </div>
      </SettingsCard>

      <SettingsCard title="Storage" sub="Where regenerated content + exports are stored.">
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: 14,
            background: 'var(--surface-2)',
            border: '1px solid var(--line)',
            borderRadius: 10,
          }}
        >
          <div
            style={{
              width: 38,
              height: 38,
              borderRadius: 8,
              background: 'var(--indigo-50)',
              color: 'var(--indigo-700)',
              display: 'grid',
              placeItems: 'center',
            }}
          >
            <Icon name="book" size={18} />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13.5, fontWeight: 600 }}>
              Vedantu S3 — content-ops-prod
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--ink-500)' }}>
              ap-south-1 · 142 GB used of 1 TB
            </div>
          </div>
          <button className="btn btn-ghost btn-sm">Manage</button>
        </div>
      </SettingsCard>
    </>
  );
}

function KeyRow({
  provider,
  keyMasked,
  usage,
}: {
  provider: string;
  keyMasked: string;
  usage: string;
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 14,
        padding: 14,
        background: 'var(--surface-2)',
        border: '1px solid var(--line)',
        borderRadius: 10,
      }}
    >
      <Icon name="key" size={18} className="muted" />
      <div style={{ flex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--ink-900)' }}>
            {provider}
          </span>
          <span className="badge ok" style={{ fontSize: 10 }}>
            <span className="dot" />
            active
          </span>
        </div>
        <div
          style={{
            fontSize: 11.5,
            color: 'var(--ink-500)',
            marginTop: 2,
            fontFamily: 'var(--font-mono)',
          }}
        >
          {keyMasked}
        </div>
      </div>
      <div style={{ textAlign: 'right' }}>
        <div style={{ fontSize: 11, color: 'var(--ink-500)' }}>{usage}</div>
        <button className="btn btn-ghost btn-sm" style={{ marginTop: 4 }}>
          Rotate
        </button>
      </div>
    </div>
  );
}

// ---------- Defaults ----------

function DefaultsSection() {
  const [selected, setSelected] = useState('cbse');

  return (
    <>
      <SettingsCard
        title="Default template"
        sub="Applied when uploading a new book unless you change it."
      >
        <div className="opt-grid">
          {TEMPLATES.map((t) => (
            <button
              key={t.id}
              onClick={() => setSelected(t.id)}
              className={`opt ${selected === t.id ? 'sel' : ''}`}
            >
              <div className="opt-check">
                <Icon name="check" size={12} />
              </div>
              <div className="opt-title">{t.name}</div>
              <div className="opt-desc">
                {t.tone} tone · {t.q}
              </div>
            </button>
          ))}
        </div>
      </SettingsCard>

      <SettingsCard title="Regeneration defaults">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 18 }}>
          <div className="field">
            <label>Variants per question</label>
            <input type="text" defaultValue="2" />
          </div>
          <div className="field">
            <label>Theory depth</label>
            <input type="text" defaultValue="Balanced" />
          </div>
        </div>
        <ToggleRow
          label="Regenerate figures with AI"
          sub="Slower but cleans up scanned diagrams"
          on
        />
        <ToggleRow
          label="Skip pages flagged as TOC / index"
          sub="Saves ~12% of processing time"
          on
        />
      </SettingsCard>
    </>
  );
}

// ---------- Security ----------

function SecuritySection() {
  return (
    <>
      <SettingsCard
        title="Sign-in"
        actions={
          <span className="badge ok">
            <span className="dot" />
            SSO active
          </span>
        }
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: 14,
            background: 'var(--surface-2)',
            border: '1px solid var(--line)',
            borderRadius: 10,
          }}
        >
          <div
            style={{
              width: 36,
              height: 36,
              display: 'grid',
              placeItems: 'center',
            }}
          >
            <GoogleG size={22} />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13.5, fontWeight: 600 }}>Google Workspace SSO</div>
            <div style={{ fontSize: 11.5, color: 'var(--ink-500)' }}>
              Restricted to @vedantu.com · enforced by IT
            </div>
          </div>
          <span className="badge ok">Required</span>
        </div>
      </SettingsCard>

      <SettingsCard title="Active sessions">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <SessionRow
            device="MacBook Pro · Chrome"
            loc="Bengaluru, IN"
            time="Active now"
            current
          />
          <SessionRow device="iPad · Safari" loc="Bengaluru, IN" time="2 days ago" />
        </div>
      </SettingsCard>

      <SettingsCard title="Audit log" sub="Last 30 days · admin only">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {AUDIT.map((r, i) => (
            <div
              key={i}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: '8px 12px',
                fontSize: 12.5,
                color: 'var(--ink-700)',
                borderTop: i === 0 ? 'none' : '1px solid var(--line-2)',
              }}
            >
              <Icon name="clock" size={12} className="muted" />
              <span
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 11,
                  color: 'var(--ink-500)',
                  minWidth: 90,
                }}
              >
                {r.t}
              </span>
              <span style={{ flex: 1 }}>{r.a}</span>
              <span className="kbd" style={{ fontSize: 10 }}>
                {r.book}
              </span>
            </div>
          ))}
        </div>
      </SettingsCard>
    </>
  );
}

const AUDIT = [
  { t: 'today, 10:42', a: 'Regenerated chapter "Graphical Method"', book: 'Quadratic Equations' },
  { t: 'today, 09:18', a: 'Exported DOCX',                          book: 'Physics for JEE Main' },
  { t: 'yesterday',    a: 'Uploaded new book',                      book: 'Class 7 Mathematics' },
];

function SessionRow({
  device,
  loc,
  time,
  current,
}: {
  device: string;
  loc: string;
  time: string;
  current?: boolean;
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: 14,
        background: 'var(--surface-2)',
        border: '1px solid var(--line)',
        borderRadius: 10,
      }}
    >
      <div
        style={{
          width: 36,
          height: 36,
          borderRadius: 8,
          background: current ? 'var(--success-bg)' : 'var(--bg-tint)',
          color: current ? 'var(--success)' : 'var(--ink-500)',
          display: 'grid',
          placeItems: 'center',
        }}
      >
        <Icon name="shield" size={16} />
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--ink-900)' }}>
          {device}{' '}
          {current && (
            <span className="badge ok" style={{ fontSize: 10, marginLeft: 6 }}>
              this session
            </span>
          )}
        </div>
        <div style={{ fontSize: 11.5, color: 'var(--ink-500)' }}>
          {loc} · {time}
        </div>
      </div>
      {!current && <button className="btn btn-ghost btn-sm">Sign out</button>}
    </div>
  );
}
