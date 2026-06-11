
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts';
import { useStore } from '../store';

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
            <Tooltip 
              contentStyle={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border-color)', borderRadius: '8px' }}
              itemStyle={{ color: 'var(--text-primary)' }}
              labelStyle={{ color: 'var(--text-secondary)' }}
            />
            <ReferenceLine y={0.9} stroke="var(--danger)" strokeDasharray="3 3" opacity={0.5} label={{ position: 'insideTopLeft', value: 'Alert Threshold', fill: 'var(--danger)', fontSize: 12 }} />
            <Area type="monotone" dataKey="deepfake_score" stroke="var(--accent-blue)" strokeWidth={2} fillOpacity={1} fill="url(#scoreGradient)" isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
};
