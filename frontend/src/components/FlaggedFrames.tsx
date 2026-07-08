
import { useEffect, useRef } from 'react';
import { Flag, AlertTriangle } from 'lucide-react';
import { useStore } from '../store';

export const FlaggedFrames = () => {
  const {
    flaggedFrames, hoveredFrameIndex,
    setSelectedFlaggedFrame, selectedStream,
  } = useStore();

  const rowRefs = useRef<Record<string, HTMLDivElement | null>>({});

  const filtered = selectedStream
    ? flaggedFrames.filter(f => f.stream_id === selectedStream)
    : flaggedFrames;

  const sorted = [...filtered].reverse();

  useEffect(() => {
    if (hoveredFrameIndex === null) return;
    const match = sorted.find(f => f.frame_index === hoveredFrameIndex);
    if (match) {
      const key = `${match.stream_id}-${match.frame_index}`;
      rowRefs.current[key]?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [hoveredFrameIndex]);

  if (filtered.length === 0) {
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

  return (
    <div className="card flex-col gap-3">
      <div className="flex-row justify-between" style={{ alignItems: 'center' }}>
        <div className="flex-row gap-2" style={{ color: 'var(--danger)' }}>
          <Flag size={18} />
          <h3 style={{ fontSize: '1rem' }}>Flagged Frames</h3>
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', alignSelf: 'center' }}>score &gt; 0.5</span>
        </div>
        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{filtered.length} flagged</span>
      </div>

      <div className="flex-col gap-2" style={{ maxHeight: '280px', overflowY: 'auto' }}>
        {sorted.map((frame) => {
          const key = `${frame.stream_id}-${frame.frame_index}`;
          const pct = Math.round(frame.deepfake_score * 100);
          const scoreColor = pct >= 90 ? 'var(--danger)' : pct >= 70 ? 'var(--warning)' : 'var(--accent-cyan)';
          const isHovered = hoveredFrameIndex === frame.frame_index;
          return (
            <div
              key={key}
              ref={(el) => { rowRefs.current[key] = el; }}
              onClick={() => setSelectedFlaggedFrame(frame.frame_index)}
              style={{
                padding: '0.5rem 0.75rem',
                background: isHovered ? 'rgba(96, 165, 250, 0.08)' : 'rgba(255,255,255,0.03)',
                borderRadius: '6px',
                border: isHovered
                  ? '1px solid rgba(96, 165, 250, 0.35)'
                  : `1px solid ${frame.alert ? 'rgba(248,113,113,0.3)' : 'var(--border-color)'}`,
                cursor: 'pointer',
                transition: 'background 0.15s, border-color 0.15s',
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

              <div style={{ background: 'rgba(255,255,255,0.06)', borderRadius: '4px', height: '5px', overflow: 'hidden' }}>
                <div style={{ width: `${pct}%`, height: '100%', background: scoreColor, borderRadius: '4px', transition: 'width 0.3s' }} />
              </div>
              <div style={{ fontSize: '0.75rem', color: scoreColor, marginTop: '3px', fontWeight: 600 }}>{pct}% fake</div>

              {frame.summary && (
                <div style={{
                  fontSize: '0.75rem',
                  color: 'var(--text-secondary)',
                  marginTop: '0.4rem',
                  lineHeight: 1.45,
                  borderTop: '1px solid var(--border-color)',
                  paddingTop: '0.35rem',
                }}>
                  {frame.summary}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
