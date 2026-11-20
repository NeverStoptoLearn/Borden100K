"""Small preflight checks; never launches experiments, deletes data, or pushes Git."""
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path
MAX_BLOB=10*1024**2
FORBIDDEN={'.venv','.venvs','node_modules','__pycache__','.pytest_cache'}
OUTPUTS={'outputs','results','checkpoints','runtime','.maintenance'}
def git(root,*args):return subprocess.check_output(['git',*args],cwd=root)
def staged_issues(root):
 names=git(root,'diff','--cached','--name-only','--diff-filter=ACMR','-z').decode('utf8').strip('\0').split('\0');issues=[]
 for name in filter(None,names):
  p=Path(name);parts=p.parts
  if any(x in FORBIDDEN for x in parts) or parts[0] in OUTPUTS or p.name in ['.env','credentials.json'] or p.suffix in ['.pem','.key']:issues.append((name,'FORBIDDEN_PAYLOAD'))
  size=int(git(root,'cat-file','-s',':'+name))
  if size>MAX_BLOB:issues.append((name,'BLOB_EXCEEDS_10_MiB'))
 return issues
def plan_issues(root,plan):
 root=Path(root).resolve();issues=[]
 for row in plan:
  p=Path(row['path'])
  if not p.is_absolute():issues.append('PATH_NOT_ABSOLUTE');continue
  resolved=p.resolve(strict=False)
  if resolved==root or not resolved.is_relative_to(root):issues.append('OUTSIDE_PROJECT_OR_ROOT');continue
  if any(x in ['.git','.maintenance'] for x in p.parts):issues.append('PROTECTED_CONTROL_PATH');continue
  for q in [p,*p.parents]:
   if q==root:break
   if q.exists() and (getattr(q.lstat(),'st_file_attributes',0)&1024 or q.is_symlink()):issues.append('REPARSE_PATH');break
 return issues
def action_issues(root,operation,plan_path=None,approval_path=None):
 if not approval_path or not plan_path:return ['EXPLICIT_PLAN_AND_HUMAN_APPROVAL_REQUIRED']
 p=Path(plan_path);a=json.loads(Path(approval_path).read_text(encoding='utf8'))
 if a.get('operation')!=operation or a.get('project')!=str(Path(root).resolve()) or a.get('plan_sha256')!=hashlib.sha256(p.read_bytes()).hexdigest() or a.get('human_approved') is not True:return ['APPROVAL_BINDING_MISMATCH']
 rows=json.loads(p.read_text(encoding='utf8'))
 return plan_issues(root,rows) if operation=='retire' else []
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=Path.cwd());sub=parser.add_subparsers(dest='command',required=True)
 sub.add_parser('staged');a=sub.add_parser('check-action');a.add_argument('--operation',choices=['experiment','retire','publish'],required=True);a.add_argument('--plan',type=Path);a.add_argument('--approval',type=Path)
 args=parser.parse_args();issues=staged_issues(args.root) if args.command=='staged' else action_issues(args.root,args.operation,args.plan,args.approval)
 print(json.dumps(dict(status='BLOCKED' if issues else 'PASS',issues=issues,check_only=True)));return 2 if issues else 0
if __name__=='__main__':raise SystemExit(main())
