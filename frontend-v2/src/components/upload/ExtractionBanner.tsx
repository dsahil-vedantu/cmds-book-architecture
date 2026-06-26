import { Icon } from '../Icon';
import type { ExtractStage, PickedFile } from '../../mocks/upload';

type Props = {
  stageIdx: number;
  stages: ExtractStage[];
  file: PickedFile;
};

export function ExtractionBanner({ stageIdx, stages, file }: Props) {
  const current = stages[Math.min(stageIdx, stages.length - 1)];

  return (
    <div
      className="card"
      style={{
        padding: 22,
        background: 'linear-gradient(180deg, var(--indigo-50), var(--surface))',
        borderColor: 'var(--indigo-100)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 14 }}>
        <div
          style={{
            width: 42,
            height: 42,
            borderRadius: 11,
            background: 'var(--indigo-700)',
            display: 'grid',
            placeItems: 'center',
            color: '#fff',
            boxShadow: '0 6px 16px -6px rgba(26,35,126,0.45)',
          }}
        >
          <span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />
        </div>
        <div style={{ flex: 1 }}>
          <div
            style={{
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: '0.1em',
              textTransform: 'uppercase',
              color: 'var(--indigo-700)',
            }}
          >
            Extracting
          </div>
          <div
            style={{
              fontSize: 15,
              fontWeight: 700,
              color: 'var(--ink-900)',
              marginTop: 2,
            }}
          >
            {current?.label}
          </div>
          <div style={{ fontSize: 12, color: 'var(--ink-500)', marginTop: 2 }}>
            {file.name} · {file.pages ?? 218} pages · {current?.detail}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div
            className="mono"
            style={{
              fontSize: 22,
              fontWeight: 800,
              color: 'var(--ink-900)',
              letterSpacing: '-0.02em',
              lineHeight: 1,
            }}
          >
            {Math.min(stageIdx, stages.length)}
            <span style={{ fontSize: 13, color: 'var(--ink-500)', fontWeight: 600 }}>
              /{stages.length}
            </span>
          </div>
          <div style={{ fontSize: 11, color: 'var(--ink-500)', marginTop: 4 }}>
            stages done
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 6 }}>
        {stages.map((s, i) => {
          const state = i < stageIdx ? 'done' : i === stageIdx ? 'now' : 'next';
          return (
            <div
              key={s.id}
              style={{
                flex: 1,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '8px 10px',
                background:
                  state === 'done'
                    ? 'var(--success-bg)'
                    : state === 'now'
                    ? 'var(--surface)'
                    : 'transparent',
                border: '1px solid',
                borderColor:
                  state === 'done'
                    ? '#A8DCC4'
                    : state === 'now'
                    ? 'var(--indigo-500)'
                    : 'var(--line)',
                borderRadius: 8,
                color:
                  state === 'done'
                    ? '#0B6A4F'
                    : state === 'now'
                    ? 'var(--indigo-700)'
                    : 'var(--ink-500)',
                boxShadow: state === 'now' ? '0 0 0 3px rgba(63,74,176,0.10)' : 'none',
                transition: 'all 240ms',
                minWidth: 0,
              }}
            >
              <div
                style={{
                  width: 18,
                  height: 18,
                  borderRadius: '50%',
                  background:
                    state === 'done'
                      ? 'var(--success)'
                      : state === 'now'
                      ? 'var(--indigo-700)'
                      : 'var(--bg-tint)',
                  display: 'grid',
                  placeItems: 'center',
                  flexShrink: 0,
                  color: state === 'next' ? 'var(--ink-400)' : '#fff',
                }}
              >
                {state === 'done' ? (
                  <Icon name="check" size={11} />
                ) : state === 'now' ? (
                  <span
                    className="spinner"
                    style={{ width: 9, height: 9, borderWidth: 1.5 }}
                  />
                ) : (
                  <span style={{ fontSize: 9.5, fontWeight: 700 }}>{i + 1}</span>
                )}
              </div>
              <span
                style={{
                  fontSize: 11.5,
                  fontWeight: 600,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {s.label
                  .replace('Extracting ', '')
                  .replace('Analyzing ', '')
                  .replace('Building ', '')}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
