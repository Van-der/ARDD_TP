// RBAC (M11): decode the JWT's role claim client-side (no round-trip needed)
// to hide/disable admin-only UI. This does NOT verify the signature — the
// server already validated it when the token was issued/used; this is purely
// for UI display decisions, never a security boundary.
export function decodeJwtRole(token: string | null): 'admin' | 'viewer' | null {
  if (!token) return null;
  try {
    const payload = token.split('.')[1];
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    const decoded = JSON.parse(json);
    return decoded.role === 'admin' || decoded.role === 'viewer' ? decoded.role : null;
  } catch {
    return null;
  }
}
