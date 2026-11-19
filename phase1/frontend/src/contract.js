import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import schema from '../../../schemas/phase1-v1.schema.json';

const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
const validate = ajv.compile(schema);
const sampleValidator = ajv.compile({ $defs: schema.$defs, $ref: '#/$defs/trajectory_sample' });
export function finite(value) {
  if (typeof value === 'number' && !Number.isFinite(value)) throw Error('Non-finite number');
  if (value && typeof value === 'object') Object.values(value).forEach(finite);
}
export function envelope(value, kind, { fixture = false } = {}) {
  finite(value);
  if (!validate(value)) throw Error(ajv.errorsText(validate.errors));
  if (value.kind !== kind) throw Error(`Expected ${kind}`);
  if (value.data_provenance.fixture && !fixture) throw Error('Fixture prohibited in production');
  const ids = new Set();
  for (const a of value.artifacts) {
    if (ids.has(a.artifact_id)) throw Error('Duplicate artifact ID');
    ids.add(a.artifact_id);
  }
  return value;
}
export function sameRun(a, b) {
  for (const k of ['run_id', 'scenario_id', 'scenario_version', 'experiment_id', 'parameter_version', 'seed']) {
    if (a[k] !== b[k]) throw Error(`Replay identity mismatch: ${k}`);
  }
}
export function trajectory(samples, route) {
  let previous = -1;
  for (const s of samples) {
    finite(s);
    if (!sampleValidator(s)) throw Error(ajv.errorsText(sampleValidator.errors));
    if (s.route_id !== route.route_id || s.vehicle_id !== route.vehicle_id || s.event_time_s <= previous) throw Error('Invalid trajectory ordering/identity');
    if (s.event_time_s < route.depart_s || s.event_time_s > route.arrival_time_s) throw Error('Trajectory outside route window');
    previous = s.event_time_s;
  }
  // SUMO samples active vehicles at simulation steps; depart/arrival endpoints may be absent.
  // Never fabricate endpoint positions to satisfy a presentation requirement.
  if (route.reachable && !samples.length) throw Error('Empty reachable trajectory');
  return samples;
}
export function statusUpdate(previous, next, options) {
  envelope(next, 'run_status', options);
  if (previous) {
    sameRun(previous, next);
    const allowed = { queued: ['queued', 'running', 'failed', 'cancelled'], running: ['running', 'succeeded', 'failed', 'cancelled'], succeeded: ['succeeded'], failed: ['failed'], cancelled: ['cancelled'] };
    if (!allowed[previous.status].includes(next.status)) throw Error('Illegal status transition');
    if (next.payload.previous_status !== previous.status) throw Error('Status history mismatch');
  }
  return next;
}
