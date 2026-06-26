import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8001";
const DISMISS_KEY = "cmds_mode_banner_dismissed";

type Mode = "mock" | "real" | "agent";

export function MockBanner() {
  const [dismissed, setDismissed] = useState(
    () =>
      typeof window !== "undefined" && localStorage.getItem(DISMISS_KEY) === "1",
  );
  const { data } = useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const r = await fetch(`${API_BASE}/api/health`);
      return r.json() as Promise<{
        status: string;
        env: string;
        anthropic_mode: Mode;
        storage_backend: string;
        task_executor: string;
      }>;
    },
  });

  if (!data || dismissed) return null;

  // "real" mode is ideal — no banner needed.
  if (data.anthropic_mode === "real") return null;

  if (data.anthropic_mode === "agent") {
    return (
      <div
        className="mock-banner"
        style={{
          background: "linear-gradient(135deg, var(--green-bg), #dcfce7)",
        }}
      >
        <span className="dot" style={{ background: "var(--green)" }} />
        <span>
          <b>Live mode (Claude subscription)</b> — using your local Claude Code
          authentication via the Agent SDK. No API key needed.
        </span>
        <button
          className="x"
          onClick={() => {
            localStorage.setItem(DISMISS_KEY, "1");
            setDismissed(true);
          }}
          title="Dismiss"
        >
          ✕
        </button>
      </div>
    );
  }

  // mock
  return (
    <div className="mock-banner">
      <span className="dot" />
      <span>
        <b>Demo mode</b> — Claude responses are simulated. Install/authenticate
        Claude Code (or paste an API key in Settings) for real extraction.
      </span>
      <button
        className="x"
        onClick={() => {
          localStorage.setItem(DISMISS_KEY, "1");
          setDismissed(true);
        }}
        title="Dismiss"
      >
        ✕
      </button>
    </div>
  );
}
