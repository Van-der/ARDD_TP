import { useState } from 'react';
import { ShieldAlert } from 'lucide-react';

interface Props {
  apiUrl: string;
  token: string;
}

// RBAC (M11): only rendered by App.tsx when the JWT's role claim is "admin".
export const AdminPanel = ({ apiUrl, token }: Props) => {
  const [status, setStatus] = useState<string | null>(null);

  const resetBreaker = async (target: 'vision' | 'rag' | 'both') => {
    setStatus('Resetting...');
    try {
      const res = await fetch(`${apiUrl}/admin/reset_breaker`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ target }),
      });
      if (res.ok) {
        const data = await res.json();
        setStatus(`Reset: ${data.reset.join(', ')}`);
      } else {
        setStatus(`Failed (${res.status})`);
      }
    } catch {
      setStatus('Request failed');
    }
  };

  return (
    <div className="card flex-col gap-2" data-testid="admin-panel">
      <h3 className="flex-row gap-2" style={{ alignItems: 'center' }}>
        <ShieldAlert size={16} /> Admin
      </h3>
      <div className="flex-row gap-2">
        <button onClick={() => resetBreaker('vision')}>Reset Vision Breaker</button>
        <button onClick={() => resetBreaker('rag')}>Reset RAG Breaker</button>
      </div>
      {status && <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>{status}</span>}
    </div>
  );
};
