import { describe, it, expect, beforeEach } from 'vitest';
import { useStore } from './store';

describe('Zustand Store', () => {
  beforeEach(() => {
    useStore.setState({ frames: [], activeAlert: false, connected: false, token: null });
  });

  it('should add a frame and cap the buffer at 100 entries', () => {
    // Add 105 frames
    for (let i = 0; i < 105; i++) {
      useStore.getState().addFrame({
        frame_index: i,
        timestamp_ms: Date.now(),
        deepfake_score: 0.5,
        audit_verdict: 'PASS',
        alert: false
      });
    }

    const frames = useStore.getState().frames;
    expect(frames.length).toBe(100);
    // The first frame should be index 5 since 0-4 were shifted out
    expect(frames[0].frame_index).toBe(5);
    expect(frames[99].frame_index).toBe(104);
  });

  it('should correctly set the active alert state based on incoming frames', () => {
    useStore.getState().addFrame({
      frame_index: 1,
      timestamp_ms: Date.now(),
      deepfake_score: 0.95,
      audit_verdict: 'FAIL',
      alert: true
    });

    expect(useStore.getState().activeAlert).toBe(true);

    useStore.getState().addFrame({
      frame_index: 2,
      timestamp_ms: Date.now(),
      deepfake_score: 0.2,
      audit_verdict: 'PASS',
      alert: false
    });

    expect(useStore.getState().activeAlert).toBe(false);
  });

  it('should update connection and token states', () => {
    useStore.getState().setConnected(true);
    expect(useStore.getState().connected).toBe(true);

    useStore.getState().setToken('mock_jwt_token');
    expect(useStore.getState().token).toBe('mock_jwt_token');
  });
});
