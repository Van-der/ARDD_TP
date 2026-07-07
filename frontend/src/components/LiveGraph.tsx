
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts';
import { useStore } from '../store';
import type { FrameData } from '../store';

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  const d: FrameData = payload[0].payload;
  const score = (d.deepfake_score * 100).toFixed(1);
  const ragBoosted = d.audit_verdict === 'FAIL';

  const verdictColor = d.audit_verdict === 'PASS'
    ? 'var(--success)'
    : d.audit_verdict === 'FAIL'
    ? 'var(--danger)'
    : 'var(--text-muted)';

  return (
    <div style={{
      background: 'var(--bg-secondary)',
      border: '1px solid var(--border-color)',
      borderRadius: '8px',
      padding: '0.75rem 1rem',
      fontSize: '0.82rem',
      minWidth: '200px',
    }}>
      <div style={{ color: 'var(--text-secondary)', marginBottom: '0.4rem', fontWeight: 600 }}>
        Frame #{label}
        {d.stream_id && <span style={{ fontWeight: 400, marginLeft: '0.4rem', color: 'var(--text-muted)' }}>· {d.stream_id}</span>}
      </div>

      {/* Main score */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.3rem' }}>
        <span style={{ color: 'var(--text-secondary)' }}>Deepfake Score</span>
        <span style={{
          fontWeight: 700,
          color: d.deepfake_score >= 0.9 ? 'var(--danger)' : d.deepfake_score >= 0.5 ? 'var(--warning)' : 'var(--success)'
        }}>
          {score}%
        </span>
      </div>

      {/* Fusion formula */}
      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.4rem', fontStyle: 'italic' }}>
        sigmoid(10.14·spatial + 7.04·freq − 8.87)
      </div>

      {/* RAG verdict */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
        <span style={{ color: 'var(--text-secondary)' }}>RAG Verdict</span>
        <span style={{ color: verdictColor, fontWeight: 600 }}>{d.audit_verdict || '—'}</span>
      </div>

      {ragBoosted && (
        <div style={{ fontSize: '0.75rem', color: 'var(--warning)', marginBottom: '0.25rem' }}>
          ↑ RAG boost applied (+15% to score)
        </div>
      )}

      {/* Alert */}
      {d.alert && (
        <div style={{ marginTop: '0.35rem', padding: '0.3rem 0.5rem', background: 'rgba(239,68,68,0.12)', borderRadius: '4px', color: 'var(--danger)', fontWeight: 600, fontSize: '0.78rem' }}>
          ⚠ ALERT — 5+ consecutive frames above 90%
        </div>
      )}
    </div>
  );
};

export const LiveGraph = () => {
  const { frames } = useStore();

  return (
    <div style={{ width: '100%', height: '100%' }}>
      {frames.length === 0 ? (
        <div className="w-full h-full flex-col justify-center gap-4" style={{ alignItems: 'center', color: 'var(--text-muted)' }}>
          <p>Waiting for stream data...</p>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={frames} margin={{ top: 20, right: 0, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="scoreGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="var(--danger)" stopOpacity={0.8}/>
                <stop offset="50%" stopColor="var(--warning)" stopOpacity={0.4}/>
                <stop offset="95%" stopColor="var(--success)" stopOpacity={0.1}/>
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
            <XAxis dataKey="frame_index" stroke="var(--text-muted)" tick={{ fill: 'var(--text-muted)', fontSize: 12 }} />
            <YAxis domain={[0, 1]} stroke="var(--text-muted)" tick={{ fill: 'var(--text-muted)', fontSize: 12 }} width={40} />
            <Tooltip content={<CustomTooltip />} />
            <ReferenceLine y={0.9} stroke="var(--danger)" strokeDasharray="3 3" opacity={0.5} label={{ position: 'insideTopLeft', value: 'Alert Threshold', fill: 'var(--danger)', fontSize: 12 }} />
            <Area type="monotone" dataKey="deepfake_score" stroke="var(--accent-blue)" strokeWidth={2} fillOpacity={1} fill="url(#scoreGradient)" isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
};
