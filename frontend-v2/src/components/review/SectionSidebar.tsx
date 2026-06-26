// Left side-nav: lists sections relevant to the current top tab.
//
// Each row has a small view (👁) icon. Clicking the row OR the icon
// loads that section's content into the main panel.

import { useState } from 'react';

import { Icon } from '../Icon';
import type { Section } from '../../api/sections';

type Props = {
  sections: Section[];
  selectedId: string | null;
  onSelect: (sectionId: string) => void;
};

// Color + tooltip for the section status dot.
//
// Important rule: a section with status='failed' but with content blocks
// (or extracted questions) is NOT a true failure — the worker DID produce
// usable content but QC flagged it. Show amber + a "review carefully"
// tooltip instead of red. True red is reserved for "no content extracted".
function getStatusDot(section: Section): { color: string; tooltip: string } {
  const blocksCount =
    Array.isArray(section.blocks) ? section.blocks.length : 0;
  // SectionOut doesn't expose extracted_questions count directly in V-Studio,
  // but presence of any embedded_figures is also a signal of content. Treat
  // any non-empty blocks array as "has content".
  const hasContent = blocksCount > 0;
  switch (section.status) {
    case 'passed':
    case 'ready':
      return { color: 'var(--success)', tooltip: 'Extracted successfully' };
    case 'failed':
      if (hasContent) {
        return {
          color: 'var(--warning)',
          tooltip: `Extracted with ${blocksCount} block${blocksCount === 1 ? '' : 's'} — QC flagged, review carefully`,
        };
      }
      return { color: 'var(--red-600)', tooltip: 'Failed — no content extracted' };
    case 'skipped':
      return { color: 'var(--ink-300)', tooltip: 'Skipped' };
    case 'extracting':
    case 'running':
      return { color: 'var(--info)', tooltip: 'Extracting…' };
    case 'pending':
      return { color: 'var(--ink-300)', tooltip: 'Queued' };
    // Synthetic — used for end-of-chapter excluded question sections.
    case 'excluded':
      return { color: 'var(--warning)', tooltip: 'Excluded from extraction' };
    default:
      return { color: 'var(--ink-300)', tooltip: section.status ?? 'unknown' };
  }
}

export function SectionSidebar({ sections, selectedId, onSelect }: Props) {
  return (
    <aside
      style={{
        width: 280,
        flexShrink: 0,
        background: 'var(--surface)',
        borderRight: '1px solid var(--line)',
        overflowY: 'auto',
        height: '100%',
        padding: '14px 0',
      }}
    >
      <div
        style={{
          padding: '0 18px 10px',
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          color: 'var(--ink-500)',
        }}
      >
        Sections · {sections.length}
      </div>
      {sections.length === 0 && (
        <div style={{ padding: '14px 18px', fontSize: 13, color: 'var(--ink-500)' }}>
          No sections to show in this tab.
        </div>
      )}
      {sections.map((s) => (
        <SectionRow
          key={s.id}
          section={s}
          selected={selectedId === s.id}
          onSelect={() => onSelect(s.id)}
        />
      ))}
    </aside>
  );
}

function SectionRow({
  section,
  selected,
  onSelect,
}: {
  section: Section;
  selected: boolean;
  onSelect: () => void;
}) {
  const [hover, setHover] = useState(false);
  const { color: dot, tooltip: dotTooltip } = getStatusDot(section);
  return (
    <div
      onClick={onSelect}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '9px 18px',
        cursor: 'pointer',
        background: selected
          ? 'var(--indigo-50)'
          : hover
          ? 'var(--bg-tint)'
          : 'transparent',
        borderLeft: selected
          ? '3px solid var(--indigo-700)'
          : '3px solid transparent',
        transition: 'background 100ms, border-color 100ms',
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: dot,
          flexShrink: 0,
        }}
        title={dotTooltip}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: selected ? 600 : 500,
            color: 'var(--ink-900)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            letterSpacing: '-0.005em',
          }}
        >
          {section.title || section.section_id}
        </div>
        <div
          style={{
            fontSize: 10.5,
            color: 'var(--ink-500)',
            fontFamily: 'var(--font-mono)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            marginTop: 2,
          }}
        >
          {section.section_id}
        </div>
      </div>
      <div
        title="View section content"
        style={{
          width: 26,
          height: 26,
          borderRadius: 6,
          display: 'grid',
          placeItems: 'center',
          color: selected ? 'var(--indigo-700)' : 'var(--ink-400)',
          background: hover || selected ? 'var(--surface)' : 'transparent',
          flexShrink: 0,
        }}
      >
        <Icon name="eye" size={14} />
      </div>
    </div>
  );
}
