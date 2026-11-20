import hashlib,importlib.util,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
HERE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('guard',HERE/'project_guard.py');g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
class SafetyChecks(unittest.TestCase):
 def test_experiment_requires_explicit_approval(self):self.assertTrue(g.action_issues(HERE,'experiment'))
 def test_publish_requires_explicit_approval(self):self.assertTrue(g.action_issues(HERE,'publish'))
 def test_retire_requires_explicit_approval(self):self.assertTrue(g.action_issues(HERE,'retire'))
 def test_outside_path_denied(self):self.assertIn('OUTSIDE_PROJECT_OR_ROOT',g.plan_issues(HERE,[{'path':str(HERE.parent/'external') }]))
 def test_project_root_delete_denied(self):self.assertIn('OUTSIDE_PROJECT_OR_ROOT',g.plan_issues(HERE,[{'path':str(HERE)}]))
 def test_relative_delete_denied(self):self.assertIn('PATH_NOT_ABSOLUTE',g.plan_issues(HERE,[{'path':'../../x'}]))
 def test_git_delete_denied(self):self.assertIn('PROTECTED_CONTROL_PATH',g.plan_issues(HERE,[{'path':str(HERE/'.git/config')}]))
 def test_wrong_approval_denied(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'plan.json').write_text('[]');(p/'a.json').write_text('{}')
   self.assertEqual(g.action_issues(p,'experiment',p/'plan.json',p/'a.json'),['APPROVAL_BINDING_MISMATCH'])
 def test_staged_large_output_secret_and_small_source(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);subprocess.run(['git','init','-q'],cwd=p,check=True)
   (p/'small.py').write_text('x=1\n');(p/'.env').write_text('DUMMY=fixture\n');(p/'outputs').mkdir();(p/'outputs/log.txt').write_text('fixture\n')
   with (p/'large.bin').open('wb') as f:f.write(b'x'*(g.MAX_BLOB+1))
   subprocess.run(['git','add','.'],cwd=p,check=True);issues=g.staged_issues(p)
   self.assertIn(('large.bin','BLOB_EXCEEDS_10_MiB'),issues);self.assertIn(('.env','FORBIDDEN_PAYLOAD'),issues);self.assertIn(('outputs/log.txt','FORBIDDEN_PAYLOAD'),issues);self.assertNotIn('small.py',[a for a,b in issues])
 def test_real_precommit_rejects_large_staged_blob(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);subprocess.run(['git','init','-q'],cwd=p,check=True)
   h=p/'.git/hooks/pre-commit'
   body='#!/bin/sh\nexec "{}" "{}" --root "$(git rev-parse --show-toplevel)" staged\n'.format(sys.executable.replace('\\','/'),(HERE/'project_guard.py').as_posix())
   h.write_text(body,encoding='utf8');h.chmod(0o755)
   (p/'large.bin').write_bytes(b'x'*(g.MAX_BLOB+1));subprocess.run(['git','add','large.bin'],cwd=p,check=True)
   r=subprocess.run(['git','-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-m','fixture'],cwd=p,capture_output=True,text=True)
   self.assertNotEqual(r.returncode,0);self.assertIn('BLOCKED',r.stdout+r.stderr)
   self.assertNotEqual(subprocess.run(['git','rev-parse','--verify','HEAD'],cwd=p,capture_output=True).returncode,0)
 def test_staged_blob_not_unstaged_worktree_size(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);subprocess.run(['git','init','-q'],cwd=p,check=True)
   f=p/'source.py';f.write_text('x=1\n');subprocess.run(['git','add','source.py'],cwd=p,check=True)
   f.write_bytes(b'x'*(g.MAX_BLOB+1));self.assertEqual(g.staged_issues(p),[])

if __name__=='__main__':unittest.main(verbosity=2)
