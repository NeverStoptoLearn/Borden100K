import json,tempfile,unittest
from pathlib import Path
from borden_tasks.history import require_raw,RawRetired

class RetirementBoundary(unittest.TestCase):
    def test_only_approved_completed_event_marks_retired(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'raw';p.write_bytes(b'raw');ledger=Path(t)/'ledger.jsonl'
            event=dict(logical_path=str(p.absolute()),state='PREPARED',approval_sha256='a'*64)
            ledger.write_text(json.dumps(event)+'\n');self.assertEqual(require_raw(p,ledger),p)
            event['state']='RETIRED';ledger.write_text(json.dumps(event)+'\n')
            with self.assertRaises(RawRetired) as caught:require_raw(p,ledger)
            self.assertEqual(caught.exception.code,'RAW_RETIRED');self.assertEqual(p.read_bytes(),b'raw')
    def test_missing_without_retirement_is_missing(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(FileNotFoundError) as caught:require_raw(Path(t)/'missing',None)
            self.assertNotIsInstance(caught.exception,RawRetired)
