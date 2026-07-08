
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, ReferenceDot } from 'recharts';
import { useStore } from '../store';
import type { FrameData } from '../store';

const CustomTooltip = ({ active, payload, label }: any) => {
  const { latestTemporalAudit } = useStore();
  if (!active || !payload?.length) return null;
  const d: FrameData = payload[0].payload;
  const speedPct = (d.deepfake_score * 100).toFixed(1);
  const speedColor = d.deepfake_score >= 0.9 ? 'var(--danger)' : d.deepfake_score >= 0.5 ? 'var(--warning)' : 'var(--success)';
  const verdictColor = d.audit_verdict === 'FAIL' ? 'var(--danger)' : d.audit_verdict === 'PASS' ? 'var(--success)' : 'var(--text-muted)';

  const tempPct = latestTemporalAudit ? (latestTemporalAudit.temporal_score * 100).toFixed(1) : null;
  const tempVerdict = latestTemporalAudit?.temporal_verdict ?? null;
  const tempColor = tempVerdict === 'FAIL' ? 'var(--danger)' : tempVerdict === 'PASS' ? 'var(--success)' : 'var(--text-muted)';

  return (
    <div style={{
      background: 'var(--bg-secondary)',
      border: '1px solid var(--border-color)',
      borderRadius: '8px',
      padding: '0.75rem 1rem',
      fontSize: '0.82rem',
      minWidth: '210px',
    }}>
      <div style={{ color: 'var(--text-secondary)', marginBottom: '0.5rem', fontWeight: 600 }}>
        Frame #{label}
        {d.stream_id && (
          <span style={{ fontWeight: 400, marginLeft: '0.4rem', color: 'var(--text-muted)' }}>
            · {d.stream_id}
          </span>
        )}
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', marginBottom: '0.25rem' }}>
        <span style={{ color: 'var(--text-secondary)' }}>Speed</span>
        <span>
          <span style={{ fontWeight: 700, color: speedColor }}>{speedPct}%</span>
          <span style={{ marginLeft: '0.4rem', color: verdictColor, fontSize: '0.75rem', fontWeight: 600 }}>
            [{d.audit_verdict || '—'}]
          </span>
        </span>
      </div>

      {tempPct !== null && (
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', marginBottom: '0.25rem' }}>
          <span style={{ color: 'var(--text-secondary)' }}>Temporal</span>
          <span>
            <span style={{ fontWeight: 700, color: tempColor }}>{tempPct}%</span>
            <span style={{ marginLeft: '0.4rem', color: tempColor, fontSize: '0.75rem', fontWeight: 600 }}>
              [{tempVerdict}]
            </span>
          </span>
        </div>
      )}

      {d.alert && (
        <div style={{ marginTop: '0.5rem', padding: '0.3rem 0.5rem', background: 'rgba(248,113,113,0.12)', borderRadius: '4px', color: 'var(--danger)', fontWeight: 600, fontSize: '0.78rem' }}>
          ⚠ ALERT — 5+ consecutive frames above 90%
        </div>
      )}
    </div>
  );
};

export const LiveGraph = () => {
  const {
    frames, flaggedFrames,
    selectedStream, setSelectedStream, activeStreams,
    setHoveredFrame, selectedFlaggedFrame,
  } = useStore();

  const displayFrames = selectedStream
    ? frames.filter(f => f.stream_id === selectedStream)
    : frames;

  const refFrame = selectedFlaggedFrame !== null
    ? displayFrames.find(f => f.frame_index === selectedFlaggedFrame)
    : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: '0.5rem' }}>
      {activeStreams.length > 1 && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: '0.5rem', flexShrink: 0 }}>
          <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Stream</label>
          <select
            value={selectedStream ?? ''}
            onChange={(e) => setSelectedStream(e.target.value || null)}
            style={{
              background: 'var(--bg-secondary)',
              border: '1px solid var(--border-color)',
              borderRadius: '6px',
              color: 'var(--text-secondary)',
              fontSize: '0.78rem',
              padding: '0.2rem 0.5rem',
              cursor: 'pointer',
              outline: 'none',
            }}
          >
            <option value="">All streams</option>
            {activeStreams.map(id => (
              <option key={id} value={id}>{id}</option>
            ))}
          </select>
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0 }}>
        {displayFrames.length === 0 ? (
          <div className="w-full h-full flex-col justify-center gap-4" style={{ alignItems: 'center', color: 'var(--text-muted)' }}>
            <p>Waiting for stream data...</p>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart
              data={displayFrames}
              margin={{ top: 20, right: 0, left: 0, bottom: 0 }}
              onMouseMove={(e: any) => {
                if (e.activePayload?.length) {
                  const frame = e.activePayload[0].payload as FrameData;
                  const isFlagged = flaggedFrames.some(
                    f => f.frame_index === frame.frame_index && f.stream_id === frame.stream_id
                  );
                  setHoveredFrame(isFlagged ? frame.frame_index : null);
                }
              }}
              onMouseLeave={() => setHoveredFrame(null)}
            >
              <defs>
                <linearGradient id="scoreGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--danger)" stopOpacity={0.7}/>
                  <stop offset="50%" stopColor="var(--warning)" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="var(--success)" stopOpacity={0.1}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" vertical={false} />
              <XAxis dataKey="frame_index" stroke="var(--text-muted)" tick={{ fill: 'var(--text-muted)', fontSize: 12 }} />
              <YAxis domain={[0, 1]} stroke="var(--text-muted)" tick={{ fill: 'var(--text-muted)', fontSize: 12 }} width={40} />
              <Tooltip content={<CustomTooltip />} />
              <ReferenceLine y={0.9} stroke="var(--danger)" strokeDasharray="3 3" opacity={0.5} label={{ position: 'insideTopLeft', value: 'Alert Threshold', fill: 'var(--danger)', fontSize: 12 }} />
              <Area type="monotone" dataKey="deepfake_score" stroke="var(--accent-blue)" strokeWidth={2} fillOpacity={1} fill="url(#scoreGradient)" isAnimationActive={false} />
              {refFrame && (
                <ReferenceDot
                  x={refFrame.frame_index}
                  y={refFrame.deepfake_score}
                  r={7}
                  fill="transparent"
                  stroke="white"
                  strokeWidth={2}
                />
              )}
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
};
