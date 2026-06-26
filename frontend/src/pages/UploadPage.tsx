import { useState } from "react";
import { useAnalyse, useBook, useJob, useUploadBook } from "../api/hooks";
import { useUI } from "../stores/ui";
import { JobProgress } from "../components/JobProgress";
import { WizardRail, type RailStep } from "../components/WizardRail";

export function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const upload = useUploadBook();
  const analyse = useAnalyse();
  const { selectedBookId, selectBook, setView } = useUI();
  const { data: book } = useBook(selectedBookId);
  const { data: job } = useJob(jobId);

  const rail: RailStep =
    job?.status === "succeeded" || book?.status === "schema_ready"
      ? "schema"
      : jobId
        ? "analyse"
        : "upload";

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    const up = await upload.mutateAsync({ file, title: title || undefined });
    selectBook(up.book_id);
    const a = await analyse.mutateAsync(up.book_id);
    setJobId(a.job_id);
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f) setFile(f);
  }

  return (
    <>
      <div className="topbar">
        <div className="bc">
          <span className="bci">Library</span>
          <span className="bcs">›</span>
          <span className="bci a">Upload new book</span>
        </div>
      </div>
      <WizardRail active={rail} />
      <div className="cnt">
        <div className="ci">
          {rail === "upload" && (
            <form onSubmit={onSubmit}>
              <div className="card">
                <div className="clbl">Title (optional)</div>
                <input
                  className="inp"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="e.g. Class 12 Physics — Ch. 5 Photoelectric Effect"
                />
              </div>

              <div className="card">
                <div className="clbl">Upload PDF</div>
                <label
                  className={`dz ${dragOver ? "over" : ""}`}
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDragOver(true);
                  }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={onDrop}
                >
                  <input
                    type="file"
                    accept="application/pdf,.pdf"
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  />
                  <div className="dz-icon">📑</div>
                  <h3>Drop your PDF here</h3>
                  <p>or click to browse</p>
                </label>

                {file && (
                  <div className="fchip" style={{ marginTop: 12 }}>
                    <span>📄</span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 600 }}>{file.name}</div>
                      <div
                        style={{
                          fontSize: "0.68rem",
                          color: "var(--text3)",
                          fontFamily: "var(--mono)",
                        }}
                      >
                        {(file.size / 1024).toFixed(1)} KB
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setFile(null)}
                      className="btn bg"
                      style={{ fontSize: "0.7rem", padding: "5px 10px" }}
                    >
                      Remove
                    </button>
                  </div>
                )}
              </div>

              <div style={{ display: "flex", gap: 9 }}>
                <button
                  type="submit"
                  className="btn bp"
                  disabled={!file || upload.isPending || analyse.isPending}
                >
                  {upload.isPending || analyse.isPending
                    ? "Uploading…"
                    : "Upload & Analyse"}
                </button>
                <button
                  type="button"
                  className="btn bg"
                  onClick={() => setView("library")}
                >
                  Cancel
                </button>
              </div>
            </form>
          )}

          {jobId && (
            <>
              <JobProgress jobId={jobId} />
              {job?.status === "succeeded" && (
                <button
                  className="btn bp"
                  style={{ marginTop: 6 }}
                  onClick={() => setView("schema")}
                >
                  Review schema →
                </button>
              )}
            </>
          )}

          {book?.analyser && (
            <div className="card">
              <div className="clbl">Document Analysis</div>
              <div className="mg">
                <MetaBox
                  label="Pages"
                  value={String(book.analyser.estimated_pages)}
                />
                <MetaBox
                  label="Words"
                  value={String(book.analyser.estimated_words)}
                />
                <MetaBox label="Type" value={book.analyser.pdf_type} />
              </div>
              <div className="cvchips">
                {book.analyser.has_equations && (
                  <span className="cvc orig">Equations</span>
                )}
                {book.analyser.has_tables && <span className="cvc orig">Tables</span>}
                {book.analyser.has_diagrams && (
                  <span className="cvc orig">Diagrams</span>
                )}
                <span className="cvc">{book.analyser.subject || "—"}</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function MetaBox({ label, value }: { label: string; value: string }) {
  return (
    <div className="mgb">
      <div className="mgv">{value}</div>
      <div className="mgl">{label}</div>
    </div>
  );
}
