import { create } from 'zustand';

export interface FrameData {
  frame_index: number;
  timestamp_ms: number;
  deepfake_score: number;
  audit_verdict: string;
  alert: boolean;
}

export interface TemporalAudit {
  window_duration_s: number;
  temporal_score: number;
  temporal_verdict: string;
  low_confidence_flag: boolean;
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

  // Audits
  latestTemporalAudit: TemporalAudit | null;
  setTemporalAudit: (audit: TemporalAudit) => void;
  
  // Alerts
  activeAlert: boolean;
  setActiveAlert: (status: boolean) => void;
}

export const useStore = create<AppState>((set) => ({
  token: null,
  setToken: (token) => set({ token }),

  connected: false,
  setConnected: (connected) => set({ connected }),
  connectionError: false,
  setConnectionError: (connectionError) => set({ connectionError }),

  frames: [],
  addFrame: (frame) => set((state) => {
    const newFrames = [...state.frames, frame];
    // Keep last 100 frames for graph performance
    if (newFrames.length > 100) newFrames.shift();
    return { 
      frames: newFrames, 
      activeAlert: frame.alert 
    };
  }),
  clearFrames: () => set({ frames: [] }),

  latestTemporalAudit: null,
  setTemporalAudit: (audit) => set({ latestTemporalAudit: audit }),

  activeAlert: false,
  setActiveAlert: (activeAlert) => set({ activeAlert })
}));
