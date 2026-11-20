import { canonicalJSON as canonical } from './canonical';
import { digest } from './artifacts';
export async function validateCustomTask(entry, metadata, parameters = null) {
  const { snapshot, record, canonical_snapshot } = entry,
    ctx = metadata.context;
  if (
    typeof canonical_snapshot !== 'string' ||
    (await digest(new TextEncoder().encode(canonical_snapshot))) !== record.task_ref.sha256 ||
    canonical(JSON.parse(canonical_snapshot)) !== canonical(snapshot)
  )
    throw Error('Custom task canonical SHA mismatch');
  for (const k of ['scenario_id', 'scenario_version', 'seed', 'network_sha256'])
    if (record[k] !== ctx[k]) throw Error('Custom task scene identity mismatch');
  if (
    record.manifest_sha256 !== metadata.manifest_sha256 ||
    record.context_sha256 !== metadata.context_sha256 ||
    record.task_ref.id !== snapshot.id ||
    record.task_ref.version !== 1 ||
    snapshot.version !== 1 ||
    snapshot.data.task_id !== snapshot.id ||
    snapshot.source.synthetic !== true
  )
    throw Error('Custom task binding mismatch');
  const op = ctx.facility_access.tasks.find(
    (t) => t.task_id === record.access.source_operation_task_id,
  );
  if (
    !op ||
    op.well_id !== snapshot.data.well_id ||
    canonical(record.access) !==
      canonical({ ...op, task_id: snapshot.id, source_operation_task_id: op.task_id }) ||
    !ctx.facility_access.depots.some((d) => d.depot_id === snapshot.data.depot_id)
  )
    throw Error('Custom task operation mismatch');
  if (
    parameters &&
    canonical(parameters.data.resources.tasks.find((t) => t.task_id === snapshot.id)) !==
      canonical(snapshot.data)
  )
    throw Error('Custom task parameter mismatch');
  return entry;
}
