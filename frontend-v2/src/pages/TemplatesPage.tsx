import { useNavigate } from 'react-router-dom';

import { Icon } from '../components/Icon';
import { TEMPLATES, type Template } from '../mocks/templates';

export default function TemplatesPage() {
  const navigate = useNavigate();
  return (
    <div className="content fade-up">
      <div className="content-narrow">
        <div className="page-header">
          <div>
            <h1 className="page-title">Templates</h1>
            <div className="page-sub">
              Style presets that drive theory tone, question patterns and figure handling
              during regeneration.
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => navigate('/library')}
              title="Back to library"
            >
              <Icon name="arrow-l" size={14} /> Back to library
            </button>
            <button className="btn btn-primary">
              <Icon name="plus" size={16} /> New template
            </button>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          {TEMPLATES.map((t) => (
            <TemplateCard key={t.id} t={t} />
          ))}
        </div>
      </div>
    </div>
  );
}

function TemplateCard({ t }: { t: Template }) {
  return (
    <div className="card card-hover" style={{ padding: 0, overflow: 'hidden' }}>
      <div
        style={{
          background: t.grad,
          color: '#fff',
          padding: '20px 22px',
          position: 'relative',
        }}
      >
        <div
          style={{
            position: 'absolute',
            inset: 0,
            background:
              'radial-gradient(400px 200px at 80% -20%, rgba(255,255,255,0.18), transparent 60%)',
          }}
        />
        <div
          style={{
            position: 'relative',
            display: 'flex',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <div
            style={{
              width: 40,
              height: 40,
              borderRadius: 10,
              background: 'rgba(255,255,255,0.20)',
              backdropFilter: 'blur(4px)',
              display: 'grid',
              placeItems: 'center',
              fontFamily: 'var(--font-mono)',
              fontWeight: 800,
              fontSize: 13,
            }}
          >
            {t.name.slice(0, 2).toUpperCase()}
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 18, fontWeight: 800, letterSpacing: '-0.02em' }}>
              {t.name}
            </div>
            <div style={{ fontSize: 11.5, opacity: 0.85, marginTop: 2 }}>{t.usage}</div>
          </div>
          {t.tag && (
            <span
              style={{
                background: 'rgba(255,255,255,0.20)',
                padding: '3px 9px',
                borderRadius: 999,
                fontSize: 11,
                fontWeight: 700,
              }}
            >
              {t.tag}
            </span>
          )}
        </div>
      </div>
      <div style={{ padding: '18px 22px' }}>
        <div style={{ fontSize: 13.5, color: 'var(--ink-700)', lineHeight: 1.55 }}>
          {t.desc}
        </div>
        <div style={{ display: 'flex', gap: 18, marginTop: 16 }}>
          <Spec label="Tone" value={t.tone} />
          <Spec label="Questions" value={t.q} />
          <Spec label="Figures" value={t.figs} />
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 18 }}>
          <button className="btn btn-soft btn-sm">Edit</button>
          <button className="btn btn-ghost btn-sm">Preview output</button>
          <button className="btn btn-ghost btn-sm" style={{ marginLeft: 'auto' }}>
            <Icon name="more" size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}

function Spec({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div
        style={{
          fontSize: 10.5,
          fontWeight: 600,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          color: 'var(--ink-500)',
        }}
      >
        {label}
      </div>
      <div
        style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink-900)', marginTop: 2 }}
      >
        {value}
      </div>
    </div>
  );
}
