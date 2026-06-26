import type { RegeneratedDiagram } from '../api/questions';

/**
 * Read-only renderer for a regenerated LaTeX/SVG diagram. Used by the Preview
 * and Composer pages to show the regenerated vector figure inline (the same
 * SVG that the Word export rasterizes and embeds in place of the original).
 *
 * Renders nothing when there is no diagram, when the model fell back to the
 * original figure, or when the SVG is empty — in those cases the caller should
 * keep showing the original embedded figure.
 */
export function DiagramPreview({
  diagram,
  compact = false,
}: {
  diagram: RegeneratedDiagram | null | undefined;
  compact?: boolean;
}) {
  if (!diagram || diagram.fallback_to_original || !diagram.svg_preview) {
    return null;
  }
  return (
    <div
      style={{
        marginTop: 10,
        padding: compact ? 8 : 12,
        borderRadius: 8,
        border: '1px solid var(--teal-200, #99f6e4)',
        background: 'var(--teal-50, #f0fdfa)',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          marginBottom: 8,
          paddingBottom: 6,
          borderBottom: '1px solid var(--teal-100, #ccfbf1)',
        }}
      >
        <span
          style={{
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            color: 'var(--teal-700, #0f766e)',
          }}
        >
          ✨ Regenerated Diagram{diagram.subject ? ` · ${diagram.subject}` : ''}
        </span>
        <span style={{ fontSize: 10, fontStyle: 'italic', color: 'var(--ink-400)' }}>
          Replaces original figure
        </span>
      </div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          background: '#fff',
          padding: 12,
          borderRadius: 6,
          border: '1px solid var(--line)',
          overflow: 'auto',
        }}
        dangerouslySetInnerHTML={{ __html: diagram.svg_preview }}
      />
      {!compact && diagram.description && (
        <div style={{ marginTop: 8, fontSize: 11, color: 'var(--ink-500)' }}>
          {diagram.description}
        </div>
      )}
    </div>
  );
}
