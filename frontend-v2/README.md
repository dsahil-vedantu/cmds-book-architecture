# V-Studio — ops-facing UI for CMDS

A clean, branded UI layer that sits **on top of** the existing CMDS extraction
service. Collapses the internal 10+ stage pipeline down to a 3-step flow for
the Vedantu content-ops team: **Upload → Review → Download**.

> **`backend/` and `frontend/` are frozen.** This app is parallel-built and
> talks to the existing backend over HTTP. No backend or original-frontend
> files are touched.

---

## What's built right now (Phase 0)

- Vite + React 18 + TypeScript scaffold (no Tailwind — the design ships its
  own token-driven CSS, ported verbatim into `src/styles/globals.css`).
- React Router with route guard.
- Google OAuth login restricted to `@vedantu.com` accounts.
- Sidebar + TopBar chrome matching the design bundle.
- Library page placeholder (full grid lands in P1).
- All other routes return a P0 placeholder so navigation works end-to-end.

Subsequent phases (P1 → P6) port the rest of the design pages and wire them
to the real backend API. See `HANDOFF.md` in the design bundle and the
chat log at `/tmp/vstudio-design/cmds/chats/chat1.md` for product intent.

---

## Dev setup

```bash
cd frontend-v2
cp .env.example .env.local
# edit .env.local — at minimum set VITE_GOOGLE_CLIENT_ID
npm install
npm run dev
# → http://localhost:5174
```

If `VITE_GOOGLE_CLIENT_ID` is left blank, the login page falls back to a
local dev shim that signs you in as `dev@vedantu.com` so the rest of the UI
remains demoable without OAuth setup. Set a real client ID before sharing
with ops.

### Google OAuth client

1. Open <https://console.cloud.google.com/apis/credentials>.
2. Create an **OAuth 2.0 Client ID** (Web application).
3. Authorized JavaScript origin: `http://localhost:5174` for dev, plus the
   production URL when V-Studio ships.
4. Paste the client ID into `VITE_GOOGLE_CLIENT_ID` in `.env.local`.

The Vedantu domain gate (`VITE_ALLOWED_DOMAIN`, default `vedantu.com`) is
enforced client-side on the userinfo email. Server-side verification will
follow in Phase 5 when the backend gains a `/auth/google` endpoint.

---

## Project structure

```
frontend-v2/
├── index.html
├── package.json
├── vite.config.ts
├── tsconfig.json
├── .env.example
└── src/
    ├── main.tsx                # App bootstrap + providers
    ├── App.tsx                 # Route table
    ├── styles/globals.css      # Ported from design bundle (672 lines)
    ├── auth/
    │   ├── AuthProvider.tsx    # localStorage-backed user context
    │   ├── RequireAuth.tsx     # Route guard
    │   └── google.ts           # ID-token decode + domain helper
    ├── components/
    │   ├── AppShell.tsx        # Sidebar + TopBar + <Outlet />
    │   ├── Sidebar.tsx
    │   ├── TopBar.tsx
    │   ├── Logo.tsx
    │   ├── Icon.tsx            # Lucide-style icon set
    │   └── Toast.tsx           # Global toast provider
    ├── pages/
    │   ├── LoginPage.tsx       # Google-only sign-in
    │   └── LibraryPage.tsx     # P0 placeholder
    ├── mocks/
    │   └── books.ts            # Sample data ported from data.jsx
    └── lib/
        └── status.tsx          # Shared status-badge helper
```

---

## Roadmap

| Phase | Scope                                       | Status |
| :---- | :------------------------------------------ | :----- |
| **P0** | Scaffold + AppShell + Login + brand tokens | ✅ done |
| P1    | Library grid (BookCard, filters, stats)     | next   |
| P2    | Book detail + Chapter review (tabs, compare)| —      |
| P3    | Upload 3-step flow (mock state machine)     | —      |
| P4    | Wire pages to real backend                  | —      |
| P5    | Backend `/auth/google` + server-side gate   | —      |
| P6    | Templates + Settings polish                 | —      |
