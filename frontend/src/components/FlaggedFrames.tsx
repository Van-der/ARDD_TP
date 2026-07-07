
import { Flag, AlertTriangle } from 'lucide-react';
import { useStore } from '../store';

export const FlaggedFrames = () => {
  const { flaggedFrames } = useStore();

  if (flaggedFrames.length === 0) {
    return (
      <div className="card flex-col gap-3">
        <div className="flex-row gap-2" style={{ color: 'var(--danger)' }}>
          <Flag size={18} />
          <h3 style={{ fontSize: '1rem' }}>Flagged Frames</h3>
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', alignSelf: 'center' }}>score &gt; 0.5</span>
        </div>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>No flagged frames yet.</span>
      </div>
    );
  }

  // Show most recent at top
  const sorted = [...flaggedFrames].reverse();

  return (
    <div className="card flex-col gap-3">
      <div className="flex-row justify-between" style={{ alignItems: 'center' }}>
        <div className="flex-row gap-2" style={{ color: 'var(--danger)' }}>
          <Flag size={18} />
          <h3 style={{ fontSize: '1rem' }}>Flagged Frames</h3>
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', alignSelf: 'center' }}>score &gt; 0.5</span>
        </div>
        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{flaggedFrames.length} flagged</span>
      </div>

      <div className="flex-col gap-2" style={{ maxHeight: '260px', overflowY: 'auto' }}>
        {sorted.map((frame) => {
          const pct = Math.round(frame.deepfake_score * 100);
          const color = pct >= 90 ? 'var(--danger)' : pct >= 70 ? 'var(--warning)' : 'var(--accent-cyan)';
          return (
            <div
              key={`${frame.stream_id}-${frame.frame_index}`}
              style={{
                padding: '0.5rem 0.75rem',
                background: 'rgba(255,255,255,0.03)',
                borderRadius: '6px',
                border: `1px solid ${frame.alert ? 'rgba(239,68,68,0.35)' : 'var(--border-color)'}`,
              }}
            >
              <div className="flex-row justify-between" style={{ alignItems: 'center', marginBottom: '0.35rem' }}>
                <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                  {frame.stream_id} · frame {frame.frame_index}
                </span>
                {frame.alert && (
                  <span style={{ fontSize: '0.7rem', color: 'var(--danger)', display: 'flex', alignItems: 'center', gap: '3px' }}>
                    <AlertTriangle size={11} /> ALERT
                  </span>
                )}
              </div>
              {/* Score bar */}
              <div style={{ background: 'rgba(255,255,255,0.06)', borderRadius: '4px', height: '6px', overflow: 'hidden' }}>
                <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: '4px', transition: 'width 0.3s' }} />
              </div>
              <div style={{ fontSize: '0.75rem', color, marginTop: '3px', fontWeight: 600 }}>{pct}% fake</div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
