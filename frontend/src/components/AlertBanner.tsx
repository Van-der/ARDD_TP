
import { ShieldAlert } from 'lucide-react';

export const AlertBanner = () => {
  return (
    <div className="card alert-pulse flex-row gap-4" style={{ background: 'rgba(239, 68, 68, 0.1)', borderColor: 'var(--danger)' }}>
      <ShieldAlert color="var(--danger)" size={32} />
      <div className="flex-col">
        <h3 style={{ color: 'var(--danger)', marginBottom: '0.25rem' }}>High-Confidence Threat Detected</h3>
        <p style={{ margin: 0, fontSize: '0.9rem', color: 'var(--text-secondary)' }}>
          Multiple consecutive frames flagged with deepfake score &gt; 90%. Stream recording segment isolated for audit.
        </p>
      </div>
    </div>
  );
};
