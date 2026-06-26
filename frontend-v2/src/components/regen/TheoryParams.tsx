// Theory regen params card. Field names + value enums mirror the
// backend's RegenParams Pydantic schema 1:1 — see backend/app/schemas/regen.py.

import { useEffect, useState } from 'react';

import { ParamLabel, ParamRow, ParamTextarea, SegmentChoice } from './PipelineCard';
import { getRecapRules, type RecapRule, type TheoryRegenParams } from '../../api/regen';

type Props = {
  value: TheoryRegenParams;
  onChange: (next: TheoryRegenParams) => void;
};

export function TheoryParams({ value, onChange }: Props) {
  const upd = <K extends keyof TheoryRegenParams>(
    k: K,
    v: TheoryRegenParams[K],
  ) => onChange({ ...value, [k]: v });

  // ─── Recap rules (v3 opt-in) ──────────────────────────────────────
  const [recapRules, setRecapRules] = useState<RecapRule[] | null>(null);
  const [recapOpen, setRecapOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getRecapRules()
      .then((rules) => {
        if (!cancelled) setRecapRules(rules);
      })
      .catch(() => {
        if (!cancelled) setRecapRules([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const activeIds = value.recap_rule_ids ?? [];
  const toggleRecap = (id: string) => {
    const next = activeIds.includes(id)
      ? activeIds.filter((x) => x !== id)
      : [...activeIds, id];
    upd('recap_rule_ids', next);
  };

  return (
    <div>
      <ParamRow>
        <ParamLabel hint="How much the rewrite changes the wording. Light = subtle paraphrase, Heavy = full rewrite.">
          Intensity
        </ParamLabel>
        <SegmentChoice
          options={['light', 'moderate', 'heavy'] as const}
          value={value.intensity}
          onChange={(v) => upd('intensity', v)}
        />
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Academic register variant. Rigorous = dense exam-prep; Pedagogical = balanced for comprehension; Interactive = formal but engaging.">
          Tone
        </ParamLabel>
        <SegmentChoice
          options={
            ['academic_rigorous', 'academic_pedagogical', 'academic_interactive'] as const
          }
          value={value.tone}
          onChange={(v) => upd('tone', v)}
          format={(v) => v.replace('academic_', '')}
        />
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Whether equations stay verbatim or get explained in prose.">
          Equations
        </ParamLabel>
        <SegmentChoice
          options={['preserve', 'explain'] as const}
          value={value.equations_handling}
          onChange={(v) => upd('equations_handling', v)}
        />
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Whether figures stay as-is or get described in text.">
          Diagrams
        </ParamLabel>
        <SegmentChoice
          options={['preserve', 'describe'] as const}
          value={value.diagrams_handling}
          onChange={(v) => upd('diagrams_handling', v)}
        />
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Whether to add real-world analogies to make concepts relatable.">
          Analogies
        </ParamLabel>
        <SegmentChoice
          options={['none', 'add_one', 'add_multiple'] as const}
          value={value.analogies}
          onChange={(v) => upd('analogies', v)}
          format={(v) => v.replace('_', ' ')}
        />
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Keep paragraph order or allow restructuring.">
          Structure
        </ParamLabel>
        <SegmentChoice
          options={['identical', 'reorganize'] as const}
          value={value.structure}
          onChange={(v) => upd('structure', v)}
        />
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Optional. Shape the rewrite for a specific audience.">
          Target audience
        </ParamLabel>
        <input
          type="text"
          value={value.target_audience ?? ''}
          onChange={(e) => upd('target_audience', e.target.value || null)}
          placeholder="e.g. Class 10 CBSE students"
          style={{
            width: '100%',
            height: 38,
            padding: '0 12px',
            border: '1px solid var(--line)',
            borderRadius: 10,
            font: 'inherit',
            fontSize: 13,
          }}
        />
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Free-form guidance appended to the prompt. E.g. 'Use Indian context examples'.">
          Custom instructions
        </ParamLabel>
        <ParamTextarea
          value={value.custom_instructions ?? ''}
          onChange={(v) => upd('custom_instructions', v || null)}
          placeholder="Any extra rules or style notes for this regeneration…"
        />
      </ParamRow>

      {recapRules && recapRules.length > 0 && (
        <div
          style={{
            marginTop: 12,
            border: '1px solid var(--line)',
            borderRadius: 10,
            overflow: 'hidden',
          }}
        >
          <button
            type="button"
            onClick={() => setRecapOpen((v) => !v)}
            style={{
              width: '100%',
              padding: '10px 14px',
              background: 'var(--ink-50, #f6f7fa)',
              border: 'none',
              borderBottom: recapOpen ? '1px solid var(--line)' : 'none',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              cursor: 'pointer',
              fontSize: 13,
              fontWeight: 600,
              color: 'var(--ink-700)',
            }}
          >
            <span>
              Recap rules{' '}
              <span
                style={{
                  fontWeight: 500,
                  color: 'var(--ink-500)',
                  fontSize: 12,
                }}
              >
                · {activeIds.length} selected · v3 only
              </span>
            </span>
            <span style={{ fontSize: 11, color: 'var(--ink-500)' }}>
              {recapOpen ? '▾' : '▸'}
            </span>
          </button>
          {recapOpen && (
            <div style={{ padding: '10px 14px 12px' }}>
              <div
                style={{
                  fontSize: 12,
                  color: 'var(--ink-500)',
                  marginBottom: 10,
                  lineHeight: 1.5,
                }}
              >
                Rename source callouts to standard names, or redistribute
                chapter-end summary bullets into their relevant topic.
                Requires backend{' '}
                <code
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: 11,
                    padding: '1px 5px',
                    background: 'var(--ink-50, #f0f1f5)',
                    borderRadius: 4,
                  }}
                >
                  THEORY_REGEN_PROMPT_VERSION=v3
                </code>
                .
              </div>
              {recapRules.map((rule) => {
                const checked = activeIds.includes(rule.id);
                const sourceHint =
                  rule.kind === 'rename'
                    ? `Source labels: ${rule.source_labels.join(', ')}`
                    : `Source sections: ${rule.source_section_patterns.join(', ')}`;
                return (
                  <label
                    key={rule.id}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 10,
                      padding: '8px 0',
                      borderTop: '1px solid var(--line)',
                      cursor: 'pointer',
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleRecap(rule.id)}
                      style={{ marginTop: 3 }}
                    />
                    <div style={{ flex: 1 }}>
                      <div
                        style={{
                          fontSize: 13,
                          fontWeight: 600,
                          color: 'var(--ink-900)',
                        }}
                      >
                        {rule.label}{' '}
                        <span
                          style={{
                            fontSize: 11,
                            fontWeight: 500,
                            color: 'var(--ink-500)',
                            fontFamily: 'var(--font-mono)',
                          }}
                        >
                          {rule.kind}
                        </span>
                      </div>
                      <div
                        style={{
                          fontSize: 11,
                          color: 'var(--ink-500)',
                          marginTop: 2,
                          fontFamily: 'var(--font-mono)',
                        }}
                      >
                        {sourceHint}
                      </div>
                      <div
                        style={{
                          fontSize: 12,
                          color: 'var(--ink-600)',
                          marginTop: 4,
                          lineHeight: 1.5,
                        }}
                      >
                        {rule.description}
                      </div>
                    </div>
                  </label>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
