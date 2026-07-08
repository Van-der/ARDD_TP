import { create } from 'zustand';

export interface FrameData {
  stream_id: string;
  frame_index: number;
  timestamp_ms: number;
  deepfake_score: number;
  audit_verdict: string;
  alert: boolean;
  matched_signature?: string | null;
  summary?: string;
}

export interface TemporalAudit {
  stream_id: string;
  window_duration_s: number;
  temporal_score: number;
  temporal_verdict: string;
  low_confidence_flag: boolean;
}

export interface VerdictCounts {
  PASS: number;
  FAIL: number;
  UNKNOWN: number;
}

interface AppState {
  // Auth
  token: string | null;
  setToken: (token: string) => void;

  // Connection
  connected: boolean;
  setConnected: (status: boolean) => void;
  connectionError: boolean;
  setConnectionError: (status: boolean) => void;

  // Data
  frames: FrameData[];
  addFrame: (frame: FrameData) => void;
  clearFrames: () => void;

  // Flagged frames (deepfake_score > 0.5), last 30 kept
  flaggedFrames: FrameData[];

  // Audits
  latestTemporalAudit: TemporalAudit | null;
  setTemporalAudit: (audit: TemporalAudit) => void;

  // Temporal service health — fetched from GET /health on WebSocket connect
  temporalServiceStatus: 'unknown' | 'ok' | 'unavailable';
  setTemporalServiceStatus: (status: 'unknown' | 'ok' | 'unavailable') => void;

  // Verdict counters
  verdictCounts: VerdictCounts;
  resetVerdictCounts: () => void;

  // Alerts — sticky: fires on first alert frame, stays until dismissed
  activeAlert: boolean;
  alertConsecutiveCount: number;
  dismissAlert: () => void;

  // Cross-panel linking: graph hover ↔ flagged frames panel
  hoveredFrameIndex: number | null;
  setHoveredFrame: (idx: number | null) => void;
  selectedFlaggedFrame: number | null;
  setSelectedFlaggedFrame: (idx: number | null) => void;

  // Stream selector
  selectedStream: string | null;
  setSelectedStream: (id: string | null) => void;
  activeStreams: string[];

  // Temporal window progress (0–20 frames accumulated since last window)
  temporalWindowProgress: number;
  streamWindowCounters: Record<string, number>;
}

export const useStore = create<AppState>((set) => ({
  token: null,
  setToken: (token) => set({ token }),

  connected: false,
  setConnected: (connected) => set({ connected }),
  connectionError: false,
  setConnectionError: (connectionError) => set({ connectionError }),

  frames: [],
  flaggedFrames: [],
  addFrame: (frame) => set((state) => {
    const newFrames = [...state.frames, frame];
    if (newFrames.length > 100) newFrames.shift();
    const newFlagged = frame.deepfake_score > 0.5
      ? [...state.flaggedFrames, frame].slice(-30)
      : state.flaggedFrames;
    const newCount = frame.alert ? state.alertConsecutiveCount + 1 : state.alertConsecutiveCount;
    const newAlert = state.activeAlert || frame.alert;
    const newActiveStreams = state.activeStreams.includes(frame.stream_id)
      ? state.activeStreams
      : [...state.activeStreams, frame.stream_id];
    const prev = state.streamWindowCounters[frame.stream_id] ?? 0;
    const newStreamCounter = Math.min(prev + 1, 20);
    const newCounters = { ...state.streamWindowCounters, [frame.stream_id]: newStreamCounter };
    const relevantStream = state.selectedStream ?? frame.stream_id;
    return {
      frames: newFrames,
      flaggedFrames: newFlagged,
      activeAlert: newAlert,
      alertConsecutiveCount: newCount,
      activeStreams: newActiveStreams,
      streamWindowCounters: newCounters,
      temporalWindowProgress: newCounters[relevantStream] ?? 0,
    };
  }),
  clearFrames: () => set({ frames: [], flaggedFrames: [] }),

  latestTemporalAudit: null,
  setTemporalAudit: (audit) => set((state) => {
    const newCounters = { ...state.streamWindowCounters, [audit.stream_id]: 0 };
    const relevantStream = state.selectedStream ?? audit.stream_id;
    return {
      latestTemporalAudit: audit,
      verdictCounts: {
        ...state.verdictCounts,
        [audit.temporal_verdict]: (state.verdictCounts[audit.temporal_verdict as keyof VerdictCounts] ?? 0) + 1,
      },
      streamWindowCounters: newCounters,
      temporalWindowProgress: newCounters[relevantStream] ?? 0,
    };
  }),

  temporalServiceStatus: 'unknown',
  setTemporalServiceStatus: (temporalServiceStatus) => set({ temporalServiceStatus }),

  verdictCounts: { PASS: 0, FAIL: 0, UNKNOWN: 0 },
  resetVerdictCounts: () => set({ verdictCounts: { PASS: 0, FAIL: 0, UNKNOWN: 0 } }),

  activeAlert: false,
  alertConsecutiveCount: 0,
  dismissAlert: () => set({ activeAlert: false, alertConsecutiveCount: 0 }),

  hoveredFrameIndex: null,
  setHoveredFrame: (hoveredFrameIndex) => set({ hoveredFrameIndex }),
  selectedFlaggedFrame: null,
  setSelectedFlaggedFrame: (selectedFlaggedFrame) => set({ selectedFlaggedFrame }),

  selectedStream: null,
  setSelectedStream: (selectedStream) => set((state) => {
    const progress = selectedStream !== null
      ? (state.streamWindowCounters[selectedStream] ?? 0)
      : (state.frames.length > 0
          ? (state.streamWindowCounters[state.frames[state.frames.length - 1].stream_id] ?? 0)
          : 0);
    return { selectedStream, temporalWindowProgress: progress };
  }),
  activeStreams: [],

  temporalWindowProgress: 0,
  streamWindowCounters: {},
}));
