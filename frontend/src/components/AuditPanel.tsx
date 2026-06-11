
import { useStore } from '../store';
import { Clock, Search, ShieldAlert, CheckCircle2, HelpCircle } from 'lucide-react';

export const AuditPanel = () => {
  const { frames, latestTemporalAudit, connected } = useStore();
  const latestFrame = frames.length > 0 ? frames[frames.length - 1] : null;

  const renderVerdictBadge = (verdict: string) => {
    switch (verdict) {
      case 'PASS': return <span className="badge badge-success"><CheckCircle2 size={12} style={{marginRight: 4}}/> Authentic</span>;
      case 'FAIL': return <span className="badge badge-danger"><ShieldAlert size={12} style={{marginRight: 4}}/> Fake</span>;
      default: return <span className="badge badge-neutral"><HelpCircle size={12} style={{marginRight: 4}}/> Unknown</span>;
    }
  };

  return (
    <div className="flex-col gap-4">
      {/* RAG Context Panel (Speed Layer) */}
      <div className="card flex-col gap-4">
        <div className="flex-row gap-2" style={{ color: 'var(--accent-cyan)' }}>
          <Search size={18} />
          <h3 style={{ fontSize: '1rem' }}>Context Agent (RAG)</h3>
        </div>
        
        {latestFrame ? (
          <div className="flex-col gap-2">
            <div className="flex-row justify-between">
              <span style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>Instant Verdict</span>
              {renderVerdictBadge(latestFrame.audit_verdict)}
            </div>
            {latestFrame.audit_verdict === 'FAIL' && (
              <div style={{ marginTop: '0.5rem', padding: '0.75rem', background: 'rgba(239, 68, 68, 0.1)', borderRadius: '8px', border: '1px solid rgba(239, 68, 68, 0.2)', fontSize: '0.85rem' }}>
                <span style={{ color: 'var(--danger)', fontWeight: 600 }}>Signature Match: </span>
                Known threat pattern detected.
              </div>
            )}
          </div>
        ) : (
          <span style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>Awaiting frame data...</span>
        )}
      </div>

      {/* Temporal Batch Panel (Batch Layer - Phase 2) */}
      <div className="card flex-col gap-4">
        <div className="flex-row gap-2" style={{ color: 'var(--warning)' }}>
          <Clock size={18} />
          <h3 style={{ fontSize: '1rem' }}>Temporal Sequence Audit</h3>
        </div>
        
        {!connected ? (
          <span style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>Offline</span>
        ) : latestTemporalAudit ? (
          <div className="flex-col gap-3">
            <div className="flex-row justify-between">
              <span style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>30s Sequence Verdict</span>
              {renderVerdictBadge(latestTemporalAudit.temporal_verdict)}
            </div>
            <div className="flex-row justify-between">
              <span style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>Continuity Score</span>
              <span style={{ fontWeight: 600 }}>{((1 - latestTemporalAudit.temporal_score) * 100).toFixed(1)}%</span>
            </div>
            
            {latestTemporalAudit.low_confidence_flag && (
              <div style={{ marginTop: '0.5rem', padding: '0.5rem', background: 'rgba(245, 158, 11, 0.1)', borderRadius: '8px', border: '1px solid rgba(245, 158, 11, 0.2)', fontSize: '0.8rem', color: 'var(--warning)' }}>
                Warning: Incomplete buffer (padding applied).
              </div>
            )}
          </div>
        ) : (
          <span style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
            Temporal Audit Unavailable — Relying on Spatial heuristics.
          </span>
        )}
      </div>
    </div>
  );
};
