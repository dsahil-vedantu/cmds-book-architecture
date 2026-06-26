// Figures regen params card. Mirrors RegenFiguresRequest in
// backend/app/api/figures.py. Note: the backend's figure regen
// endpoint is PER-SECTION — V-Studio loops over all sections with
// figures when "regenerate all" is selected.

import {
  ParamLabel,
  ParamRow,
  ParamTextarea,
  SegmentChoice,
} from './PipelineCard';
import type { FiguresRegenParams } from '../../api/regen';

type Props = {
  value: FiguresRegenParams;
  onChange: (next: FiguresRegenParams) => void;
};

export function FiguresParams({ value, onChange }: Props) {
  const upd = <K extends keyof FiguresRegenParams>(
    k: K,
    v: FiguresRegenParams[K],
  ) => onChange({ ...value, [k]: v });

  return (
    <div>
      <ParamRow>
        <ParamLabel hint="Enhanced = cleaned-up AI redraw. Original = re-OCR'd raster.">
          Style
        </ParamLabel>
        <SegmentChoice
          options={['enhanced', 'original'] as const}
          value={value.style}
          onChange={(v) => upd('style', v)}
        />
      </ParamRow>

      <ParamRow>
        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            cursor: 'pointer',
            padding: '10px 14px',
            border: '1px solid var(--line)',
            borderRadius: 10,
            background: 'var(--surface-2)',
          }}
        >
          <input
            type="checkbox"
            checked={value.overlay ?? true}
            onChange={(e) => upd('overlay', e.target.checked)}
          />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink-900)' }}>
              Overlay labels
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--ink-500)' }}>
              Re-render axis labels and annotations cleanly on top of the redraw.
            </div>
          </div>
        </label>
      </ParamRow>

      <ParamRow>
        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            cursor: 'pointer',
            padding: '10px 14px',
            border: '1px solid var(--line)',
            borderRadius: 10,
            background: 'var(--warning-bg)',
          }}
        >
          <input
            type="checkbox"
            checked={value.watermark_clean ?? false}
            onChange={(e) => upd('watermark_clean', e.target.checked)}
          />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink-900)' }}>
              Watermark cleanup
            </div>
            <div style={{ fontSize: 11.5, color: '#8A5300' }}>
              Off by default — Gemini's safety filter often rejects watermark
              removal. The v2 pipeline already redraws cleanly without this.
            </div>
          </div>
        </label>
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Free-form guidance for the figure regenerator.">
          Custom instructions
        </ParamLabel>
        <ParamTextarea
          value={value.custom_instructions ?? ''}
          onChange={(v) => upd('custom_instructions', v || null)}
          placeholder="e.g. Use a sans-serif font for labels. Match Class-10 textbook style."
        />
      </ParamRow>
    </div>
  );
}
