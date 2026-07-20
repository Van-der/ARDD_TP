import { useState, useEffect, useRef } from 'react';
import { ShieldCheck, Activity, ServerCrash, AlertCircle, Sun, Moon } from 'lucide-react';
import { useStore } from './store';
import { decodeJwtRole } from './jwt';
import { LiveGraph } from './components/LiveGraph';
import { AuditPanel } from './components/AuditPanel';
import { AlertBanner } from './components/AlertBanner';
import { FlaggedFrames } from './components/FlaggedFrames';
import { AdminPanel } from './components/AdminPanel';
import './index.css';

// Read URLs from Vite env vars (set in docker-compose or .env)
// Fallback to localhost for local development outside Docker
// RBAC (M11): defaults to the read-only "viewer" role pair. Set
// VITE_CLIENT_ID/VITE_CLIENT_SECRET to the admin pair to see admin-only UI.
const CLIENT_ID = import.meta.env.VITE_CLIENT_ID ?? 'viewer';
const CLIENT_SECRET = import.meta.env.VITE_CLIENT_SECRET ?? 'viewer-secret-change-me';
// https/wss (M10 mTLS) — the local CA must be trusted once in-browser, see
// scripts/gen_certs.sh's printed instructions, or these requests will fail
// with a cert error.
const API_URL = import.meta.env.VITE_API_URL ?? 'https://localhost:8003';
const WS_URL = import.meta.env.VITE_WS_URL ?? 'wss://localhost:8003/stream';

const MAX_RECONNECT_ATTEMPTS = 8;
const BASE_BACKOFF_MS = 500;

export default function App() {
  const [isDark, setIsDark] = useState(true);

  const toggleTheme = () => {
    document.documentElement.classList.toggle('light');
    setIsDark(d => !d);
  };

  const {
    token, setToken,
    connected, setConnected,
    connectionError, setConnectionError,
    addFrame, setTemporalAudit,
    setTemporalServiceStatus,
    activeAlert, dismissAlert
  } = useStore();

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptsRef = useRef<number>(0);
  const maxRetriesExhausted = useRef<boolean>(false);
  // SEC-4: refresh_token doesn't need to be React state — it's never rendered,
  // only read by refreshAccessToken(), so a ref avoids an extra re-render.
  const refreshTokenRef = useRef<string | null>(null);

  // JWT Auth logic — retries once on failure before giving up
  const authenticate = async (isRetry = false): Promise<void> => {
    try {
      const res = await fetch(`${API_URL}/auth/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: CLIENT_ID, client_secret: CLIENT_SECRET })
      });
      if (res.ok) {
        const data = await res.json();
        setToken(data.access_token);
        refreshTokenRef.current = data.refresh_token;
      } else if (!isRetry) {
        console.warn('Auth failed, retrying once...');
        await authenticate(true);
      } else {
        console.error('Auth failed after retry. Manual re-login required.');
        setConnectionError(true);
      }
    } catch (e) {
      if (!isRetry) {
        await authenticate(true);
      } else {
        console.error('Auth exception after retry:', e);
        setConnectionError(true);
      }
    }
  };

  // SEC-4: access tokens are short-lived (15 min) — refresh via the rotating
  // refresh_token instead of a full re-login; falls back to authenticate()
  // (full re-login) if the refresh token itself is missing/expired/revoked.
  const refreshAccessToken = async (): Promise<void> => {
    if (!refreshTokenRef.current) {
      await authenticate();
      return;
    }
    try {
      const res = await fetch(`${API_URL}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshTokenRef.current })
      });
      if (res.ok) {
        const data = await res.json();
        setToken(data.access_token);
        refreshTokenRef.current = data.refresh_token;
      } else {
        await authenticate();
      }
    } catch (e) {
      console.warn('Token refresh failed, falling back to full re-login:', e);
      await authenticate();
    }
  };

  // Connect WebSocket with true exponential backoff
  useEffect(() => {
    if (!token) {
      authenticate();
      return;
    }

    const connect = () => {
      if (wsRef.current?.readyState === WebSocket.OPEN) return;
      if (maxRetriesExhausted.current) return;

      const ws = new WebSocket(WS_URL, [token]);

      ws.onopen = async () => {
        setConnected(true);
        setConnectionError(false);
        reconnectAttemptsRef.current = 0;
        maxRetriesExhausted.current = false;
        // Fetch temporal service health from aggregation-service on connect
        try {
          const resp = await fetch(`${API_URL}/health`);
          if (resp.ok) {
            const data = await resp.json();
            setTemporalServiceStatus(data.temporal_service_status === 'ok' ? 'ok' : 'unavailable');
          } else {
            setTemporalServiceStatus('unavailable');
          }
        } catch {
          setTemporalServiceStatus('unavailable');
        }
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'temporal_audit') {
            setTemporalAudit(data);
          } else if (data.deepfake_score !== undefined) {
            addFrame(data);
          }
        } catch (e) {
          console.error('Malformed WebSocket event — discarding:', e);
        }
      };

      ws.onclose = () => {
        setConnected(false);
        setConnectionError(true);

        const attempt = reconnectAttemptsRef.current;

        if (attempt >= MAX_RECONNECT_ATTEMPTS) {
          maxRetriesExhausted.current = true;
          console.error(`WebSocket: max reconnect attempts (${MAX_RECONNECT_ATTEMPTS}) reached. Manual refresh required.`);
          return;
        }

        // True exponential backoff: 500ms, 1s, 2s, 4s, 8s, 16s, 30s cap
        const delay = Math.min(BASE_BACKOFF_MS * Math.pow(2, attempt), 30000);
        console.warn(`WebSocket closed. Reconnect attempt ${attempt + 1}/${MAX_RECONNECT_ATTEMPTS} in ${delay}ms`);
        reconnectAttemptsRef.current = attempt + 1;
        reconnectTimeoutRef.current = setTimeout(connect, delay);
      };

      wsRef.current = ws;
    };

    connect();

    // SEC-4: access token TTL is 900s (15 min) — refresh at 800s, comfortably
    // before expiry, via the rotating refresh token instead of re-authenticating.
    const refreshInterval = setInterval(() => refreshAccessToken(), 800 * 1000);

    return () => {
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      clearInterval(refreshInterval);
      wsRef.current?.close();
    };
  }, [token]);

  return (
    <div className="w-full flex-col gap-6">
      <header className="flex-row justify-between w-full" style={{ paddingBottom: '1rem', borderBottom: '1px solid var(--border-color)' }}>
        <div className="flex-row gap-4">
          <div style={{ background: 'var(--accent-blue)', width: '40px', height: '40px', borderRadius: '8px', display: 'flex', placeItems: 'center', justifyContent: 'center' }}>
            <Activity color="white" />
          </div>
          <div className="flex-col">
            <h1 style={{ fontSize: '1.5rem' }}>ARDD-TP</h1>
            <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Real-Time Threat Detection</span>
          </div>
        </div>

        <div className="flex-row gap-4">
          {maxRetriesExhausted.current && (
            <div className="badge badge-danger flex-row gap-2">
              <AlertCircle size={14} /> Connection Failed — Refresh Page
            </div>
          )}
          {!maxRetriesExhausted.current && !connected && connectionError && (
            <div className="badge badge-warning flex-row gap-2">
              <ServerCrash size={14} /> Stale Data — Reconnecting
            </div>
          )}
          {connected && (
            <div className="badge badge-success flex-row gap-2">
              <ShieldCheck size={14} /> Live Stream
            </div>
          )}
          <button
            onClick={toggleTheme}
            title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
            style={{
              background: 'var(--bg-secondary)',
              border: '1px solid var(--border-color)',
              borderRadius: '8px',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              padding: '0.4rem 0.6rem',
              display: 'flex',
              alignItems: 'center',
              transition: 'background 0.2s',
            }}
          >
            {isDark ? <Sun size={16} /> : <Moon size={16} />}
          </button>
        </div>
      </header>

      {activeAlert && <AlertBanner onDismiss={dismissAlert} />}

      <main style={{ display: 'grid', gridTemplateColumns: '1fr 350px', gap: '1.5rem', marginTop: '2rem' }}>
        <section className="flex-col gap-4">
          <h2>Live Telemetry</h2>
          <div className="glass-panel" style={{ height: '500px', padding: '1rem' }}>
            <LiveGraph />
          </div>
          <FlaggedFrames />
        </section>

        <aside className="flex-col gap-4">
          <h2>Audit Reports</h2>
          <AuditPanel />
          {decodeJwtRole(token) === 'admin' && <AdminPanel apiUrl={API_URL} token={token!} />}
        </aside>
      </main>
    </div>
  );
}
