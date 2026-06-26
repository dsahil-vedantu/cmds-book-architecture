import { useState } from "react";
import { useProviders, useSaveProviderKeys } from "../api/hooks";
import type { Provider } from "../api/client";

export function SettingsPage() {
  const { data: providers, isLoading } = useProviders();

  return (
    <>
      <div className="topbar">
        <div className="bc">
          <span className="bci a">OCR Providers</span>
        </div>
      </div>
      <div className="cnt">
        <div className="ci">
          <div
            style={{
              fontSize: "0.76rem",
              color: "var(--text3)",
              marginBottom: 12,
            }}
          >
            Keys are encrypted at rest (Fernet) and never exposed to the browser.
            Unconfigured providers automatically fall back to Anthropic.
          </div>

          {isLoading && <div style={{ color: "var(--text3)" }}>Loading…</div>}

          {providers?.map((p) => (
            <ProviderCard key={p.name} provider={p} />
          ))}
        </div>
      </div>
    </>
  );
}

function ProviderCard({ provider }: { provider: Provider }) {
  const save = useSaveProviderKeys();
  const [editing, setEditing] = useState(false);
  const [keys, setKeys] = useState<Record<string, string>>({});

  const fields: string[] =
    provider.name === "anthropic"
      ? ["api_key"]
      : provider.name === "mathpix"
        ? ["app_id", "app_key"]
        : provider.name === "sarvam"
          ? ["api_key"]
          : provider.name === "google_vision"
            ? ["credentials_json", "gcs_bucket"]
            : [];

  async function onSave() {
    await save.mutateAsync({ name: provider.name, keys });
    setEditing(false);
  }

  const badge = provider.healthy
    ? { cls: "pc-badge ok", label: "Connected" }
    : provider.configured
      ? { cls: "pc-badge warn", label: "Unreachable" }
      : { cls: "pc-badge off", label: "Not configured" };

  return (
    <div className="card">
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
        }}
      >
        <div>
          <div
            style={{
              fontWeight: 700,
              color: "var(--text)",
              fontSize: "0.92rem",
              marginBottom: 2,
            }}
          >
            {provider.name}
          </div>
          <div style={{ fontSize: "0.7rem", color: "var(--text3)" }}>
            {provider.handles.join(", ")}
          </div>
          <div
            style={{
              fontSize: "0.66rem",
              color: "var(--text3)",
              fontFamily: "var(--mono)",
              marginTop: 2,
            }}
          >
            ~{provider.avg_time_per_page}s/page
          </div>
        </div>
        <span className={badge.cls}>{badge.label}</span>
      </div>

      {provider.message && (
        <div
          style={{
            fontSize: "0.7rem",
            color: "var(--text3)",
            marginTop: 8,
          }}
        >
          {provider.message}
        </div>
      )}

      {fields.length > 0 && (
        <div style={{ marginTop: 10 }}>
          {!editing ? (
            <button
              className="btn bg"
              style={{ fontSize: "0.72rem", padding: "5px 10px" }}
              onClick={() => setEditing(true)}
            >
              {provider.configured ? "Update keys" : "Configure"}
            </button>
          ) : (
            <div
              style={{
                marginTop: 8,
                paddingTop: 8,
                borderTop: "1px solid var(--border)",
              }}
            >
              {fields.map((f) => (
                <div key={f} style={{ marginBottom: 8 }}>
                  <div
                    style={{
                      fontSize: "0.6rem",
                      fontWeight: 700,
                      textTransform: "uppercase",
                      letterSpacing: 0.6,
                      color: "var(--text3)",
                      marginBottom: 3,
                    }}
                  >
                    {f}
                  </div>
                  <input
                    className="inp"
                    type={
                      f.includes("key") || f.includes("credentials")
                        ? "password"
                        : "text"
                    }
                    value={keys[f] ?? ""}
                    onChange={(e) =>
                      setKeys({ ...keys, [f]: e.target.value })
                    }
                  />
                </div>
              ))}
              <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                <button
                  className="btn bp"
                  style={{ fontSize: "0.72rem", padding: "5px 10px" }}
                  onClick={onSave}
                  disabled={save.isPending}
                >
                  Save
                </button>
                <button
                  className="btn bg"
                  style={{ fontSize: "0.72rem", padding: "5px 10px" }}
                  onClick={() => setEditing(false)}
                >
                  Cancel
                </button>
              </div>
              {save.data && (
                <div
                  style={{
                    fontSize: "0.7rem",
                    marginTop: 6,
                    color: save.data.valid
                      ? "var(--green)"
                      : "var(--red)",
                  }}
                >
                  {save.data.valid
                    ? "Saved and validated ✓"
                    : "Saved but the credentials could not be validated"}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
