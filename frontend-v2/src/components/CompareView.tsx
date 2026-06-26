import { useRef } from 'react';

import { Icon } from './Icon';
import { DIFF_TEXT, ORIGINAL_THEORY, REGEN_THEORY } from '../mocks/chapterContent';

export function CompareView() {
  const leftRef = useRef<HTMLDivElement>(null);
  const rightRef = useRef<HTMLDivElement>(null);
  // requestAnimationFrame lock prevents a feedback loop where each pane's
  // scroll handler triggers the other pane's handler in an endless cascade.
  const lock = useRef(false);

  const syncFrom = (src: React.RefObject<HTMLDivElement>, dst: React.RefObject<HTMLDivElement>) =>
    () => {
      if (lock.current) return;
      if (!src.current || !dst.current) return;
      lock.current = true;
      dst.current.scrollTop = src.current.scrollTop;
      requestAnimationFrame(() => {
        lock.current = false;
      });
    };

  return (
    <div style={{ height: 'calc(100vh - 260px)', minHeight: 480 }}>
      <div className="cmp-grid">
        {/* Original */}
        <div className="cmp-pane">
          <div className="cmp-pane-head" style={{ background: 'var(--surface-2)' }}>
            <Icon name="file" size={14} /> <span>Original</span>
            <span className="kbd" style={{ marginLeft: 'auto' }}>from PDF</span>
          </div>
          <div
            className="cmp-pane-body"
            ref={leftRef}
            onScroll={syncFrom(leftRef, rightRef)}
          >
            {ORIGINAL_THEORY.map((s) => (
              <div key={s.id}>
                <h4>{s.heading}</h4>
                <p>
                  {DIFF_TEXT.filter((d) => d.type !== 'add').map((d, i) =>
                    d.type === 'del' ? (
                      <del key={i} className="diff-del">{d.text}</del>
                    ) : (
                      <span key={i}>{d.text}</span>
                    )
                  )}
                </p>
                <p>{s.body}</p>
                <p>
                  This passage continues with additional explanation that the original PDF
                  presented in a formal, textbook tone. Detail and exposition are preserved
                  verbatim from the source material so that the ops team can reference the
                  canonical wording at any time.
                </p>
              </div>
            ))}
          </div>
        </div>

        {/* Regenerated */}
        <div className="cmp-pane">
          <div className="cmp-pane-head" style={{ background: 'var(--red-50)' }}>
            <Icon name="sparkles" size={14} style={{ color: 'var(--red-700)' }} />
            <span style={{ color: 'var(--red-700)' }}>Regenerated</span>
            <span className="kbd" style={{ marginLeft: 'auto' }}>CBSE · variants ×2</span>
          </div>
          <div
            className="cmp-pane-body"
            ref={rightRef}
            onScroll={syncFrom(rightRef, leftRef)}
          >
            {REGEN_THEORY.map((s) => (
              <div key={s.id}>
                <h4>{s.heading}</h4>
                <p>
                  {DIFF_TEXT.filter((d) => d.type !== 'del').map((d, i) =>
                    d.type === 'add' ? (
                      <ins key={i} className="diff-add">{d.text}</ins>
                    ) : (
                      <span key={i}>{d.text}</span>
                    )
                  )}
                </p>
                <p>{s.body}</p>
                <p>
                  The regenerated passage keeps every formula, fact and figure reference intact
                  while tightening the prose for a Class 10 reader.{' '}
                  <ins className="diff-add">
                    It also adds a brief link to the algebraic interpretation
                  </ins>
                  , which the source text leaves implicit.
                </p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
