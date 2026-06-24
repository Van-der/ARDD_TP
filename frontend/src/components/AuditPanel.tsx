
import { useStore } from '../store';
import { Clock, Search, ShieldAlert, CheckCircle2, HelpCircle, BarChart2 } from 'lucide-react';

export const AuditPanel = () => {
  const { frames, latestTemporalAudit, connected, verdictCounts, resetVerdictCounts, temporalServiceStatus } = useStore();
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
        ) : latestTemporalAudit !== null ? (
          <div className="flex-col gap-3">
            <div className="flex-row justify-between">
              <span style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>20-Frame Sequence Verdict</span>
              {renderVerdictBadge(latestTemporalAudit.temporal_verdict)}
            </div>
            <div className="flex-row justify-between">
              <span style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>Authenticity Confidence</span>
              <span style={{
                fontWeight: 600,
                color: (1 - latestTemporalAudit.temporal_score) >= 0.6
                  ? 'var(--success)'
                  : (1 - latestTemporalAudit.temporal_score) >= 0.4
                  ? 'var(--warning)'
                  : 'var(--danger)'
              }}>
                {((1 - latestTemporalAudit.temporal_score) * 100).toFixed(1)}% confident real
              </span>
            </div>
            
            {latestTemporalAudit.low_confidence_flag && (
              <div style={{ marginTop: '0.5rem', padding: '0.5rem', background: 'rgba(245, 158, 11, 0.1)', borderRadius: '8px', border: '1px solid rgba(245, 158, 11, 0.2)', fontSize: '0.8rem', color: 'var(--warning)' }}>
                Warning: Incomplete buffer (padding applied).
              </div>
            )}
          </div>
        ) : temporalServiceStatus === 'unavailable' ? (
          <span style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
            Temporal Audit Unavailable — Relying on Spatial heuristics.
          </span>
        ) : (
          <span style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
            Awaiting first batch window (~0.67s per 20 frames)…
          </span>
        )}
      </div>
      {/* Verdict Tally */}
      <div className="card flex-col gap-3">
        <div className="flex-row justify-between" style={{ alignItems: 'flex-start' }}>
          <div className="flex-col gap-1">
            <div className="flex-row gap-2" style={{ color: 'var(--text-secondary)' }}>
              <BarChart2 size={18} />
              <h3 style={{ fontSize: '1rem' }}>Temporal Verdict Tally</h3>
            </div>
            <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', paddingLeft: '26px' }}>20-frame sequence windows</span>
          </div>
          <button
            onClick={resetVerdictCounts}
            style={{ fontSize: '0.75rem', color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: '2px 6px', borderRadius: '4px' }}
          >
            Reset
          </button>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.5rem' }}>
          <div style={{ textAlign: 'center', padding: '0.6rem', background: 'rgba(34,197,94,0.08)', borderRadius: '8px', border: '1px solid rgba(34,197,94,0.2)' }}>
            <div style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--success)' }}>{verdictCounts.PASS}</div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '2px' }}>Real windows</div>
          </div>
          <div style={{ textAlign: 'center', padding: '0.6rem', background: 'rgba(239,68,68,0.08)', borderRadius: '8px', border: '1px solid rgba(239,68,68,0.2)' }}>
            <div style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--danger)' }}>{verdictCounts.FAIL}</div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '2px' }}>Fake windows</div>
          </div>
          <div style={{ textAlign: 'center', padding: '0.6rem', background: 'rgba(107,114,128,0.08)', borderRadius: '8px', border: '1px solid rgba(107,114,128,0.2)' }}>
            <div style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--text-muted)' }}>{verdictCounts.UNKNOWN}</div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '2px' }}>Unknown</div>
          </div>
        </div>

        {(verdictCounts.PASS + verdictCounts.FAIL) > 0 && (
          <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', textAlign: 'center' }}>
            {verdictCounts.PASS + verdictCounts.FAIL} audits —{' '}
            <span style={{ color: 'var(--danger)' }}>
              {((verdictCounts.FAIL / (verdictCounts.PASS + verdictCounts.FAIL)) * 100).toFixed(1)}% flagged fake
            </span>
          </div>
        )}
      </div>
    </div>
  );
};
