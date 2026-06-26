import { useJob } from "../api/hooks";
import type { UUID } from "../api/client";

export function JobProgress({ jobId }: { jobId: UUID }) {
  const { data: job } = useJob(jobId);
  if (!job) return null;
  const pct = Math.max(0, Math.min(100, job.progress));
  return (
    <div className="card">
      <div className="clbl">
        {job.type}{" "}
        <span style={{ color: "var(--text3)", fontWeight: 500 }}>— {job.status}</span>
      </div>
      <div className="prog">
        <div
          className="progb"
          style={{
            width: `${pct}%`,
            background:
              job.status === "failed"
                ? "linear-gradient(90deg, #dc2626, #b91c1c)"
                : undefined,
          }}
        />
      </div>
      <div className="progr">
        <span style={{ maxWidth: "80%", overflow: "hidden", textOverflow: "ellipsis" }}>
          {job.message || " "}
        </span>
        <span>{pct}%</span>
      </div>
      {job.error && (
        <div
          style={{
            marginTop: 8,
            color: "var(--red)",
            fontSize: "0.72rem",
            fontFamily: "var(--mono)",
          }}
        >
          {job.error}
        </div>
      )}
    </div>
  );
}
