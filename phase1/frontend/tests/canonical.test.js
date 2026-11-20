import { describe, it, expect } from 'vitest';
import { canonicalJSON } from '../src/canonical';

describe('JSON snapshot identity', () => {
  it('ignores object key order at every depth, preserving array order', () => {
    expect(canonicalJSON({ b: [{ z: 3, a: 1 }], a: null })).toBe(
      canonicalJSON({ a: null, b: [{ a: 1, z: 3 }] }),
    );
    expect(canonicalJSON([1, 2])).not.toBe(canonicalJSON([2, 1]));
  });
  it('preserves all JSON scalar types and exact Unicode values', () => {
    for (const value of [null, false, true, 0, -0, 1.5, '场景', [], {}]) {
      expect(JSON.parse(canonicalJSON(value))).toEqual(JSON.parse(JSON.stringify(value)));
    }
    expect(canonicalJSON({ seed: 42 })).not.toBe(canonicalJSON({ seed: '42' }));
    expect(canonicalJSON({ rate: 1 })).not.toBe(canonicalJSON({ rate: 2 }));
  });
});
