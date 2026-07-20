import { describe, it, expect } from 'vitest';
import { decodeJwtRole } from './jwt';

function makeJwt(payload: object): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.fakesignature`;
}

describe('decodeJwtRole', () => {
  it('extracts role=admin from a valid token', () => {
    expect(decodeJwtRole(makeJwt({ role: 'admin', sub: 'admin' }))).toBe('admin');
  });

  it('extracts role=viewer from a valid token', () => {
    expect(decodeJwtRole(makeJwt({ role: 'viewer', sub: 'viewer' }))).toBe('viewer');
  });

  it('returns null for a token with no role claim', () => {
    expect(decodeJwtRole(makeJwt({ sub: 'someone' }))).toBeNull();
  });

  it('returns null for an unrecognized role value', () => {
    expect(decodeJwtRole(makeJwt({ role: 'superadmin' }))).toBeNull();
  });

  it('returns null for a null token', () => {
    expect(decodeJwtRole(null)).toBeNull();
  });

  it('returns null for a malformed token', () => {
    expect(decodeJwtRole('not-a-jwt')).toBeNull();
  });
});
