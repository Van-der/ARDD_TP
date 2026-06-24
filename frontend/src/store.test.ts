import { describe, it, expect, beforeEach } from 'vitest';
import { useStore } from './store';

const makeFrame = (overrides = {}) => ({
  stream_id: 'test_stream',
  frame_index: 0,
  timestamp_ms: Date.now(),
  deepfake_score: 0.5,
  audit_verdict: 'PASS',
  alert: false,
  ...overrides,
});

describe('Zustand Store', () => {
  beforeEach(() => {
    useStore.setState({
      frames: [],
      flaggedFrames: [],
      activeAlert: false,
      alertConsecutiveCount: 0,
      connected: false,
      token: null,
      temporalServiceStatus: 'unknown',
    });
  });

  it('should add a frame and cap the buffer at 100 entries', () => {
    for (let i = 0; i < 105; i++) {
      useStore.getState().addFrame(makeFrame({ frame_index: i }));
    }

    const frames = useStore.getState().frames;
    expect(frames.length).toBe(100);
    expect(frames[0].frame_index).toBe(5);
    expect(frames[99].frame_index).toBe(104);
  });

  it('should set activeAlert to true when an alert frame arrives', () => {
    useStore.getState().addFrame(makeFrame({ alert: true, deepfake_score: 0.95, audit_verdict: 'FAIL' }));
    expect(useStore.getState().activeAlert).toBe(true);
  });

  it('should keep activeAlert sticky until dismissAlert is called', () => {
    useStore.getState().addFrame(makeFrame({ alert: true }));
    expect(useStore.getState().activeAlert).toBe(true);

    // A non-alert frame does NOT clear the sticky alert
    useStore.getState().addFrame(makeFrame({ alert: false, deepfake_score: 0.2 }));
    expect(useStore.getState().activeAlert).toBe(true);

    // Only dismissAlert clears it
    useStore.getState().dismissAlert();
    expect(useStore.getState().activeAlert).toBe(false);
    expect(useStore.getState().alertConsecutiveCount).toBe(0);
  });

  it('should update connection and token states', () => {
    useStore.getState().setConnected(true);
    expect(useStore.getState().connected).toBe(true);

    useStore.getState().setToken('mock_jwt_token');
    expect(useStore.getState().token).toBe('mock_jwt_token');
  });

  it('should update temporalServiceStatus via setTemporalServiceStatus', () => {
    expect(useStore.getState().temporalServiceStatus).toBe('unknown');

    useStore.getState().setTemporalServiceStatus('ok');
    expect(useStore.getState().temporalServiceStatus).toBe('ok');

    useStore.getState().setTemporalServiceStatus('unavailable');
    expect(useStore.getState().temporalServiceStatus).toBe('unavailable');
  });
});
