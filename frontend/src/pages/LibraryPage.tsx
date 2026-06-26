import { useBooks, useDeleteBook } from "../api/hooks";
import { useUI } from "../stores/ui";

export function LibraryPage() {
  const { data: books, isLoading } = useBooks();
  const del = useDeleteBook();
  const { selectBook, setView, selectedBookId } = useUI();

  const orphans = books?.filter((b) => b.status === "uploaded") ?? [];

  async function onDelete(id: string, title: string, e: React.MouseEvent) {
    e.stopPropagation();
    if (
      !confirm(
        `Delete "${title}"? This also removes all extracted sections and regenerations.`,
      )
    ) {
      return;
    }
    await del.mutateAsync(id);
    if (selectedBookId === id) selectBook(null);
  }

  async function onCleanupOrphans() {
    if (!confirm(`Delete ${orphans.length} unanalysed upload(s)? They have no schema or extracted content.`)) return;
    for (const b of orphans) {
      await del.mutateAsync(b.id);
      if (selectedBookId === b.id) selectBook(null);
    }
  }

  return (
    <>
      <div className="topbar">
        <div className="bc">
          <span className="bci a">Library</span>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {orphans.length > 0 && (
            <button
              className="btn bg"
              onClick={onCleanupOrphans}
              title={`${orphans.length} unanalysed upload(s) — click to delete`}
              style={{ fontSize: "0.72rem", color: "var(--red)" }}
            >
              🗑 Clean up {orphans.length} orphan{orphans.length > 1 ? "s" : ""}
            </button>
          )}
          <button className="btn bp" onClick={() => setView("upload")}>
            + New book
          </button>
        </div>
      </div>
      <div className="cnt">
        <div className="ci">
          {isLoading && (
            <div className="empty">
              <div className="empty-i">⏳</div>
              <h3>Loading library…</h3>
            </div>
          )}

          {!isLoading && (!books || books.length === 0) && (
            <div className="empty">
              <div className="empty-i">📂</div>
              <h3>No books yet</h3>
              <p>
                Upload a PDF to start extracting theory. Everything — schema,
                QC, regeneration — runs from one chapter at a time.
              </p>
              <button
                className="btn bp"
                style={{ marginTop: 16 }}
                onClick={() => setView("upload")}
              >
                Upload your first PDF
              </button>
            </div>
          )}

          {books &&
            books.length > 0 &&
            books.map((b) => (
              <div
                key={b.id}
                className="card"
                style={{
                  cursor: "pointer",
                  border: "1px solid var(--border)",
                  background: "var(--surface)",
                }}
                onClick={() => {
                  selectBook(b.id);
                  setView(b.status === "schema_ready" ? "schema" : "reader");
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "flex-start",
                    gap: 10,
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: "0.96rem",
                        fontWeight: 700,
                        color: "var(--text)",
                        marginBottom: 4,
                      }}
                    >
                      {b.title}
                    </div>
                    <div style={{ fontSize: "0.72rem", color: "var(--text3)" }}>
                      {b.subject || "—"}
                    </div>
                  </div>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      flexShrink: 0,
                    }}
                  >
                    <StatusChip status={b.status} />
                    <button
                      type="button"
                      onClick={(e) => onDelete(b.id, b.title, e)}
                      title="Delete book"
                      style={{
                        border: "none",
                        background: "transparent",
                        color: "var(--text3)",
                        cursor: "pointer",
                        fontSize: "0.95rem",
                        padding: "2px 6px",
                        borderRadius: 4,
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.background = "#fef2f2";
                        e.currentTarget.style.color = "var(--red)";
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.background = "transparent";
                        e.currentTarget.style.color = "var(--text3)";
                      }}
                    >
                      🗑
                    </button>
                  </div>
                </div>
              </div>
            ))}
        </div>
      </div>
    </>
  );
}

function StatusChip({ status }: { status: string }) {
  const map: Record<string, { cls: string; label: string }> = {
    ready: { cls: "cvc ok", label: "ready" },
    schema_ready: { cls: "cvc orig", label: "schema ready" },
    extracting: { cls: "cvc orig", label: "extracting" },
    analysing: { cls: "cvc orig", label: "analysing" },
    regenerating: { cls: "cvc regen", label: "regenerating" },
    failed: { cls: "cvc fail", label: "failed" },
    uploaded: { cls: "cvc", label: "uploaded" },
  };
  const info = map[status] ?? { cls: "cvc", label: status };
  return <span className={info.cls}>{info.label}</span>;
}
