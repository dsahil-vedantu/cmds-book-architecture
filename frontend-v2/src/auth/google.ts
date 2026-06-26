/**
 * Lightweight client-side decoder for a Google ID token (JWT).
 *
 * NOTE: this is **not** a cryptographic verification — it only decodes the
 * payload so we can show the user their email/name and enforce the Vedantu
 * domain gate in the UI. The real verification happens server-side when the
 * backend `/auth/google` endpoint is wired up (P5).
 */
export type GoogleIdTokenPayload = {
  email: string;
  email_verified: boolean;
  name: string;
  picture?: string;
  hd?: string; // hosted domain — present for Google Workspace accounts
};

export function decodeGoogleIdToken(token: string): GoogleIdTokenPayload | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    const payload = parts[1];
    // base64url → base64
    const b64 = payload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
    const json = atob(padded);
    return JSON.parse(decodeURIComponent(escape(json))) as GoogleIdTokenPayload;
  } catch {
    return null;
  }
}

export function isAllowedDomain(email: string, allowed: string): boolean {
  return email.toLowerCase().endsWith(`@${allowed.toLowerCase()}`);
}
