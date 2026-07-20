// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import App from './App';
import { useStore } from './store';

// Minimal WebSocket stub — App.tsx only needs the handler slots and .close();
// jsdom has no real WebSocket transport, and we don't want a real connection.
class MockWebSocket {
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  readyState = 0;
  constructor(_url: string, _protocols?: string[]) {}
  close() {}
}
vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket);
vi.stubGlobal(
  'fetch',
  vi.fn(() =>
    Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ temporal_service_status: 'ok' }),
    } as Response)
  )
);

function makeJwt(role: string): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = btoa(JSON.stringify({ role, sub: role, exp: Date.now() / 1000 + 3600 }));
  return `${header}.${body}.fakesignature`;
}

describe('AdminPanel visibility (RBAC, M11)', () => {
  beforeEach(() => {
    useStore.setState({
      token: null,
      connected: false,
      frames: [],
      flaggedFrames: [],
      activeAlert: false,
      alertConsecutiveCount: 0,
      temporalServiceStatus: 'unknown',
    });
  });

  afterEach(cleanup);

  it('does not render the admin panel for a viewer token', () => {
    useStore.setState({ token: makeJwt('viewer') });
    render(<App />);
    expect(screen.queryByTestId('admin-panel')).toBeNull();
  });

  it('renders the admin panel for an admin token', () => {
    useStore.setState({ token: makeJwt('admin') });
    render(<App />);
    expect(screen.getByTestId('admin-panel')).toBeTruthy();
  });
});
