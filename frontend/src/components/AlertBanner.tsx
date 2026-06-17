
import { ShieldAlert, X } from 'lucide-react';
import { useStore } from '../store';

interface Props {
  onDismiss: () => void;
}

export const AlertBanner = ({ onDismiss }: Props) => {
  const { alertConsecutiveCount } = useStore();

  return (
    <div className="card alert-pulse flex-row gap-4" style={{ background: 'rgba(239, 68, 68, 0.1)', borderColor: 'var(--danger)', alignItems: 'center' }}>
      <ShieldAlert color="var(--danger)" size={32} style={{ flexShrink: 0 }} />
      <div className="flex-col" style={{ flex: 1 }}>
        <h3 style={{ color: 'var(--danger)', marginBottom: '0.25rem' }}>
          High-Confidence Threat Detected
          {alertConsecutiveCount > 0 && (
            <span style={{ marginLeft: '0.75rem', fontSize: '0.85rem', fontWeight: 400 }}>
              ({alertConsecutiveCount} consecutive alert frames)
            </span>
          )}
        </h3>
        <p style={{ margin: 0, fontSize: '0.9rem', color: 'var(--text-secondary)' }}>
          Multiple consecutive frames flagged with deepfake score &gt; 90%. Stream segment isolated for audit.
        </p>
      </div>
      <button
        onClick={onDismiss}
        title="Dismiss alert"
        style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: '4px', flexShrink: 0 }}
      >
        <X size={18} />
      </button>
    </div>
  );
};
