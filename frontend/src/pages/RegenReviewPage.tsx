import { useState, useMemo } from "react";
import {
  useSections,
  useRegeneration,
  useRerunSection,
  useSaveRegeneration,
  useBookRegenerations,
} from "../api/hooks";
import type { Block, UUID } from "../api/client";
import { BlockRenderer } from "../components/BlockRenderer";
import { useUI } from "../stores/ui";

type SectionStatus = "pending" | "confirmed" | "skipped";

interface Props {
  bookId: UUID;
  regenId: UUID;
  onBack: () => void;
}

export function RegenReviewPage({ bookId, regenId, onBack }: Props) {
  const { data: regen, refetch: refetchRegen } = useRegeneration(regenId);
  const { data: sections } = useSections(bookId);
  const { setRegenId, selectSection, setView } = useUI();
  const rerun = useRerunSection();
  const save = useSaveRegeneration();
  const { refetch: refetchRegenerations } = useBookRegenerations(bookId);

  const [activeSectionId, setActiveSectionId] = useState<string | null>(null);
  const [statuses, setStatuses] = useState<Record<string, SectionStatus>>({});
  const [customInstructions, setCustomInstructions] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);

  const blocksBySection = (regen?.blocks_by_section ?? {}) as Record<string, Block[]>;
  const qcDrift = (regen?.qc_drift ?? {}) as Record<string, { pass: boolean; drifted: string[] }>;

  const sectionMap = useMemo(
    () => new Map((sections ?? []).map((s) => [s.section_id, s])),
    [sections],
  );

  const regenSectionIds = useMemo(() => Object.keys(blocksBySection), [blocksBySection]);

  const activeId = activeSectionId ?? regenSectionIds[0] ?? null;
  const activeSection = activeId ? sectionMap.get(activeId) : null;
  const activeRegenBlocks: Block[] = activeId ? (blocksBySection[activeId] ?? []) : [];
  const activeOrigBlocks: Block[] = activeSection?.blocks ?? [];
  const activeQC = activeId ? qcDrift[activeId] : null;
  const activeInstructions = activeId ? (customInstructions[activeId] ?? "") : "";
  const activeStatus = activeId ? (statuses[activeId] ?? "pending") : "pending";

  const confirmedIds = regenSectionIds.filter((id) => statuses[id] === "confirmed");
  const skippedIds = regenSectionIds.filter((id) => statuses[id] === "skipped");
  const pendingCount = regenSectionIds.filter((id) => !statuses[id] || statuses[id] === "pending").length;

  function setStatus(id: string, status: SectionStatus) {
    setStatuses((prev) => ({ ...prev, [id]: status }));
    // Auto-advance to next pending section
    const currentIdx = regenSectionIds.indexOf(id);
    const next = regenSectionIds.slice(currentIdx + 1).find((sid) => !statuses[sid] || statuses[sid] === "pending");
    if (next) setActiveSectionId(next);
  }

  async function handleRerun() {
    if (!activeId) return;
    await rerun.mutateAsync({
      regenId,
      sectionId: activeId,
      customInstructions: activeInstructions,
    });
    await refetchRegen();
  }

  async function handleSave() {
    await save.mutateAsync({ regenId, confirmedSectionIds: confirmedIds });
    await refetchRegenerations();
    // Navigate to reader in regen view, first confirmed section
    const firstConfirmedSectionId = confirmedIds[0];
    const firstSection = firstConfirmedSectionId ? sectionMap.get(firstConfirmedSectionId) : null;
    setRegenId(regenId);
    if (firstSection) selectSection(firstSection.id);
    setView("reader");
    setSaved(true);
  }

  if (!regen || !sections) {
    return (
      <div className="cnt">
        <div className="ci">
          <div className="empty"><div className="empty-i">⏳</div><h3>Loading…</h3></div>
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", height: "100%", overflow: "hidden" }}>
      {/* Left: section navigator */}
      <div style={{
        width: 220, flexShrink: 0, borderRight: "1px solid var(--border)",
        background: "var(--surface)", display: "flex", flexDirection: "column", overflow: "hidden",
      }}>
        <div style={{ padding: "12px 14px 8px", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
          <div style={{ fontSize: "0.65rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "1px", color: "var(--text3)", marginBottom: 6 }}>
            Review progress
          </div>
          <div style={{ display: "flex", gap: 8, fontSize: "0.72rem" }}>
            <span style={{ color: "var(--green)", fontWeight: 600 }}>✅ {confirmedIds.length}</span>
            <span style={{ color: "var(--text3)" }}>⏭ {skippedIds.length}</span>
            <span style={{ color: "var(--amber)", fontWeight: 600 }}>● {pendingCount}</span>
          </div>
          <div style={{ marginTop: 8, height: 3, background: "var(--surface3)", borderRadius: 99, overflow: "hidden" }}>
            <div style={{
              height: "100%", borderRadius: 99,
              background: "linear-gradient(90deg, var(--green), var(--accent))",
              width: `${regenSectionIds.length ? (confirmedIds.length / regenSectionIds.length) * 100 : 0}%`,
              transition: "width 0.3s",
            }} />
          </div>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "6px 0" }}>
          {regenSectionIds.map((sid) => {
            const sec = sectionMap.get(sid);
            const st = statuses[sid] ?? "pending";
            const isActive = sid === activeId;
            const qc = qcDrift[sid];
            return (
              <button
                key={sid}
                onClick={() => setActiveSectionId(sid)}
                style={{
                  display: "flex", alignItems: "center", gap: 7,
                  width: "calc(100% - 12px)", margin: "1px 6px",
                  padding: "6px 9px", borderRadius: 7, border: "1px solid",
                  borderColor: isActive ? "var(--accent)" : "transparent",
                  background: isActive ? "var(--accent-bg)" : "transparent",
                  cursor: "pointer", textAlign: "left", fontFamily: "var(--sans)",
                }}
              >
                <span style={{ flexShrink: 0, fontSize: "0.85rem" }}>
                  {st === "confirmed" ? "✅" : st === "skipped" ? "⏭" : qc && !qc.pass ? "⚠️" : "●"}
                </span>
                <span style={{
                  fontSize: "0.72rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  color: isActive ? "var(--accent)" : st === "confirmed" ? "var(--green)" : st === "skipped" ? "var(--text3)" : "var(--text2)",
                  fontWeight: isActive ? 600 : 400,
                }}>
                  {sec?.title ?? sid}
                </span>
              </button>
            );
          })}
        </div>
        {/* Save / back buttons */}
        <div style={{ padding: "10px 12px", borderTop: "1px solid var(--border)", flexShrink: 0, display: "flex", flexDirection: "column", gap: 7 }}>
          {confirmedIds.length > 0 && !saved && (
            <button
              className="btn bp"
              style={{ width: "100%", justifyContent: "center", fontSize: "0.76rem", padding: "8px 10px" }}
              onClick={handleSave}
              disabled={save.isPending}
            >
              {save.isPending ? "Saving…" : `✨ Save ${confirmedIds.length} section${confirmedIds.length !== 1 ? "s" : ""}`}
            </button>
          )}
          {saved && (
            <div style={{ fontSize: "0.73rem", color: "var(--green)", fontWeight: 600, textAlign: "center" }}>
              ✅ Saved to Regenerated folder
            </div>
          )}
          <button className="btn bg" style={{ width: "100%", justifyContent: "center", fontSize: "0.74rem" }} onClick={onBack}>
            ← Back to params
          </button>
        </div>
      </div>

      {/* Right: side-by-side review */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {!activeId ? (
          <div className="empty" style={{ margin: "auto" }}>
            <div className="empty-i">🔍</div>
            <h3>Select a section to review</h3>
          </div>
        ) : (
          <>
            {/* Section header + actions */}
            <div style={{
              padding: "12px 18px", borderBottom: "1px solid var(--border)",
              background: "var(--surface)", display: "flex", alignItems: "center", gap: 10, flexShrink: 0,
            }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 700, fontSize: "0.95rem", color: "var(--text)" }}>
                  {activeSection?.title ?? activeId}
                </div>
                <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                  {activeQC && (
                    <span className={`cvc ${activeQC.pass ? "ok" : "fail"}`}>
                      {activeQC.pass ? "QC pass" : `⚠ drifted: ${activeQC.drifted.join(", ")}`}
                    </span>
                  )}
                  <span className={`cvc ${activeStatus === "confirmed" ? "ok" : activeStatus === "skipped" ? "" : "regen"}`}>
                    {activeStatus}
                  </span>
                </div>
              </div>
              <button
                className="btn bg"
                style={{ borderColor: "#bbf7d0", color: "var(--green)", fontWeight: 700 }}
                onClick={() => setStatus(activeId, "confirmed")}
                disabled={activeStatus === "confirmed"}
              >
                ✅ Confirm
              </button>
              <button
                className="btn bg"
                style={{ color: "var(--text3)" }}
                onClick={() => setStatus(activeId, "skipped")}
                disabled={activeStatus === "skipped"}
              >
                ⏭ Skip
              </button>
            </div>

            {/* Side by side content */}
            <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
              {/* Original */}
              <div style={{ flex: 1, borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", overflow: "hidden" }}>
                <div style={{
                  padding: "8px 16px", background: "var(--accent-bg)", borderBottom: "1px solid #bfdbfe",
                  fontSize: "0.68rem", fontWeight: 700, color: "var(--accent)", letterSpacing: "0.8px", textTransform: "uppercase", flexShrink: 0,
                }}>
                  📄 Original
                </div>
                <div style={{ flex: 1, overflowY: "auto", padding: "16px" }}>
                  {activeOrigBlocks.length > 0
                    ? <BlockRenderer blocks={activeOrigBlocks} />
                    : <div style={{ color: "var(--text3)", fontSize: "0.8rem" }}>No original blocks.</div>
                  }
                </div>
              </div>

              {/* Regenerated */}
              <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
                <div style={{
                  padding: "8px 16px", background: "var(--purple-bg)", borderBottom: "1px solid #ddd6fe",
                  fontSize: "0.68rem", fontWeight: 700, color: "var(--purple)", letterSpacing: "0.8px", textTransform: "uppercase", flexShrink: 0,
                }}>
                  ✨ Regenerated
                </div>
                <div style={{ flex: 1, overflowY: "auto", padding: "16px" }}>
                  {rerun.isPending && rerun.variables?.sectionId === activeId ? (
                    <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--text3)", fontSize: "0.82rem", padding: "20px 0" }}>
                      <div style={{ width: 16, height: 16, border: "2px solid var(--purple)", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.7s linear infinite" }} />
                      Re-running section…
                    </div>
                  ) : activeRegenBlocks.length > 0
                    ? <BlockRenderer blocks={activeRegenBlocks} />
                    : <div style={{ color: "var(--text3)", fontSize: "0.8rem" }}>No regenerated blocks.</div>
                  }
                </div>
              </div>
            </div>

            {/* Custom instructions + re-run bar */}
            <div style={{
              padding: "10px 16px", borderTop: "1px solid var(--border)",
              background: "var(--surface2)", display: "flex", gap: 9, alignItems: "flex-end", flexShrink: 0,
            }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: "0.62rem", fontWeight: 700, color: "var(--text3)", textTransform: "uppercase", letterSpacing: "0.8px", marginBottom: 5 }}>
                  Custom instructions for this section
                </div>
                <textarea
                  className="inp"
                  rows={2}
                  placeholder="e.g. Make it more concise, use simpler vocabulary, add a real-world example…"
                  value={activeInstructions}
                  onChange={(e) => setCustomInstructions((prev) => ({ ...prev, [activeId]: e.target.value }))}
                  style={{ resize: "none", fontSize: "0.78rem" }}
                />
              </div>
              <button
                className="btn bp"
                style={{ flexShrink: 0, alignSelf: "flex-end" }}
                onClick={handleRerun}
                disabled={rerun.isPending}
              >
                {rerun.isPending && rerun.variables?.sectionId === activeId ? "Running…" : "↺ Re-run section"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
