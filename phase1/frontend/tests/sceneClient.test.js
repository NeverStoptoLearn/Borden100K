import { describe, it, expect } from 'vitest';
import { latestRequests, selectedPackage } from '../src/sceneClient';
const scene = {
  scenario_id: 'borden_reference_100k',
  scenario_version: 1,
  seed: 20261109,
  manifest_sha256: 'a'.repeat(64),
  availability: { no_pump: { allowed: true }, normal: { allowed: false } },
};
function packet() {
  return {
    catalog_version: 'dual-scenes-v1',
    scene: structuredClone(scene),
    display: {
      display_version: 'dual-display-v1',
      scenario_id: scene.scenario_id,
      scenario_version: 1,
    },
    scenario: { scenario_id: scene.scenario_id, scenario_version: 1, seed: scene.seed },
    parameters: {
      no_pump: { version: 1, data: { scenario: { id: scene.scenario_id, version: 1 } } },
      normal: null,
    },
  };
}
describe('scene isolation', () => {
  it('aborts A and rejects its late completion after B starts', async () => {
    const gate = latestRequests(),
      a = gate.begin();
    let finish;
    const late = new Promise((resolve) => (finish = resolve));
    const b = gate.begin();
    expect(a.signal.aborted).toBe(true);
    finish('old');
    await late;
    expect(a.current()).toBe(false);
    expect(b.current()).toBe(true);
    gate.cancel();
    expect(b.current()).toBe(false);
  });
  it('rejects mismatched scene,version,manifest,seed and parameter ownership', () => {
    for (const mutate of [
      (p) => (p.scene.scenario_id = 'synthetic_multiterrain_100k'),
      (p) => (p.display.scenario_version = 2),
      (p) => (p.scene.manifest_sha256 = 'b'.repeat(64)),
      (p) => (p.scenario.seed = 0),
      (p) => (p.parameters.no_pump.data.scenario.id = 'other'),
    ]) {
      const p = packet();
      mutate(p);
      expect(() => selectedPackage(p, scene)).toThrow();
    }
  });
  it('accepts matching immutable package and refuses blocked parameter bundle', () => {
    expect(selectedPackage(packet(), scene).scene).toEqual(scene);
    const p = packet();
    p.parameters.normal = p.parameters.no_pump;
    expect(() => selectedPackage(p, scene)).toThrow('Blocked');
  });
});
