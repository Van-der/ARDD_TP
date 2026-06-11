import { useEffect, useRef } from 'react';
import { ShieldCheck, Activity, ServerCrash } from 'lucide-react';
import { useStore } from './store';
import { LiveGraph } from './components/LiveGraph';
import { AuditPanel } from './components/AuditPanel';
import { AlertBanner } from './components/AlertBanner';
import './index.css';

// Using mock auth for this milestone to test WebSocket connection
const CLIENT_ID = 'test_client';
const CLIENT_SECRET = 'test_secret';
const API_URL = 'http://localhost:8003';
const WS_URL = 'ws://localhost:8003/stream';

export default function App() {
  const { 
    token, setToken, 
    connected, setConnected, 
    connectionError, setConnectionError,
    addFrame, setTemporalAudit,
    activeAlert
  } = useStore();
  
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // JWT Auth logic
  const authenticate = async () => {
    try {
      const res = await fetch(`${API_URL}/auth/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: CLIENT_ID, client_secret: CLIENT_SECRET })
      });
      if (res.ok) {
        const data = await res.json();
        setToken(data.access_token);
      }
    } catch (e) {
      console.error("Auth failed:", e);
    }
  };

  // Connect WebSocket
  useEffect(() => {
    if (!token) {
      authenticate();
      return;
    }

    const connect = () => {
      if (wsRef.current?.readyState === WebSocket.OPEN) return;
      
      const ws = new WebSocket(`${WS_URL}?token=${token}`);
      
      ws.onopen = () => {
        setConnected(true);
        setConnectionError(false);
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
          console.error("Parse error:", e);
        }
      };
      
      ws.onclose = () => {
        setConnected(false);
        setConnectionError(true);
        // Exponential backoff mock
        reconnectTimeoutRef.current = setTimeout(connect, 3000);
      };

      wsRef.current = ws;
    };

    connect();

    // JWT Refresh (Token expires in 3600s, refresh at 3300s)
    const refreshInterval = setInterval(authenticate, 3300 * 1000);

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
          {!connected && connectionError && (
            <div className="badge badge-danger flex-row gap-2">
              <ServerCrash size={14} /> Stale Data - Reconnecting
            </div>
          )}
          {connected && (
            <div className="badge badge-success flex-row gap-2">
              <ShieldCheck size={14} /> Live Stream
            </div>
          )}
        </div>
      </header>

      {activeAlert && <AlertBanner />}

      <main style={{ display: 'grid', gridTemplateColumns: '1fr 350px', gap: '1.5rem', marginTop: '2rem' }}>
        <section className="flex-col gap-4">
          <h2>Live Telemetry</h2>
          <div className="glass-panel" style={{ height: '500px', padding: '1rem' }}>
            <LiveGraph />
          </div>
        </section>
        
        <aside className="flex-col gap-4">
          <h2>Audit Reports</h2>
          <AuditPanel />
        </aside>
      </main>
    </div>
  );
}
