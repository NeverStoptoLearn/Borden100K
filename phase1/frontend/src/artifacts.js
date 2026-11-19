import { unzipSync } from 'fflate';
import { finite } from './contract';
export async function digest(bytes) { return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join(''); }
// Fetch only the artifact-ID endpoint: path is metadata, never a client URL.
export async function artifactBytes(message, id, fetcher=fetch) {
  const a=message.artifacts.find(a=>a.artifact_id===id);
  if (!a || !/^[A-Za-z0-9][A-Za-z0-9_./-]*$/.test(a.path) || a.path.split('/').some(p=>p==='.'||p==='..'||!p)) throw Error('Invalid artifact');
  const response=await fetcher(`/api/v1/runs/${encodeURIComponent(message.run_id)}/artifacts/${encodeURIComponent(id)}`);
  if (!response.ok) throw Error(`Artifact HTTP ${response.status}`);
  const bytes=new Uint8Array(await response.arrayBuffer());
  if (await digest(bytes)!==a.sha256) throw Error('Artifact SHA256 mismatch');
  return bytes;
}
function npy(bytes) {
  if (!bytes || String.fromCharCode(...bytes.slice(1,6))!=='NUMPY') throw Error('Invalid NPY');
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  const version=bytes[6];
  const start=version===1?10:12;
  const size=version===1?view.getUint16(8,true):view.getUint32(8,true);
  const header=new TextDecoder().decode(bytes.slice(start,start+size));
  const dtype=header.match(/'descr':\s*'([^']+)'/)?.[1];
  if (!['<f8','<i8','<i4'].includes(dtype) || !/'fortran_order':\s*False/.test(header)) throw Error('Expected little-endian float64/int cell IDs, C order');
  const shape=header.match(/'shape':\s*\(([^)]*)\)/)?.[1].split(',').map(s=>s.trim()).filter(Boolean).map(Number);
  if (!shape?.length || shape.some(s=>!Number.isSafeInteger(s)||s<1)) throw Error('Invalid NPY shape');
  const width=dtype==='<i4'?4:8, count=shape.reduce((a,b)=>a*b,1), offset=start+size;
  if (offset+count*width!==bytes.length) throw Error('NPY shape mismatch');
  const data=Array.from({length:count},(_,i)=>dtype==='<f8'?view.getFloat64(offset+i*width,true):dtype==='<i8'?Number(view.getBigInt64(offset+i*width,true)):view.getInt32(offset+i*width,true));
  finite(data);
  return { shape,data,dtype };
}
// Match the physical artifact verifier tolerance; keep small roundoff values unchanged.
export const CONCENTRATION_NEGATIVE_TOLERANCE_MG_L = 1e-7;
export function concentrationNPZ(bytes, message) {
  const files=unzipSync(bytes);
  const t=npy(files['times_day.npy']), c=npy(files['C.npy']), xyz=npy(files['cell_xyz_m.npy']), ids=npy(files['cell_ids.npy']);
  if ([t,c,xyz].some(a=>a.dtype!=='<f8') || t.shape.length!==1 || ids.shape.length!==1 || c.shape.length!==2 || xyz.shape.length!==2 || xyz.shape[1]!==3 || c.shape[0]!==t.data.length || c.shape[1]!==ids.data.length || xyz.shape[0]!==ids.data.length) throw Error('Invalid concentration array dimensions');
  if (ids.data.some(n=>!Number.isSafeInteger(n)||n<0) || new Set(ids.data).size!==ids.data.length || c.data.some(n=>n < -CONCENTRATION_NEGATIVE_TOLERANCE_MG_L) || t.data.some((n,i)=>n<0||(i&&n<=t.data[i-1]))) throw Error('Invalid concentration values');
  if (JSON.stringify(t.data)!==JSON.stringify(message.payload.times_day)) throw Error('Hydro sample times mismatch');
  return { times_day:t.data, C:Array.from({length:t.data.length},(_,i)=>c.data.slice(i*ids.data.length,(i+1)*ids.data.length)), cell_xyz_m:Array.from({length:ids.data.length},(_,i)=>xyz.data.slice(i*3,i*3+3)),cell_ids:ids.data,model_signature:message.payload.model_signature };
}
export function concentrationFrame(data, day) {
  const i=data.times_day.findLastIndex(t=>t<=day);
  return i<0?null:{ day:data.times_day[i], values:data.C[i], cells:data.cell_xyz_m };
}
