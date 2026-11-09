import sys,unittest
from pathlib import Path
from types import SimpleNamespace as S
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from modules.PeerRenewalHistory import expiry_transition

def job(identifier,date,expired=None):
    return S(JobID=identifier,Field='date',Action='restrict',Operator='lgt',Value=date,ExpireDate=expired,CreationDate='2026-11-01')
class EffectiveExpiry(unittest.TestCase):
    def test_extra_later_job_does_not_extend_earlier_active_limit(self):
        before,after=expiry_transition([job('old','2026-12-01')],job('new','2027-01-01'))
        self.assertEqual(before,after)
    def test_updated_job_is_real_extension(self):
        before,after=expiry_transition([job('old','2026-12-01')],job('old','2027-01-01'))
        self.assertGreater(after,before)
    def test_deleted_old_job_is_historic_evidence(self):
        before,after=expiry_transition([job('old','2026-12-01','2026-11-09')],job('new','2027-01-01'))
        self.assertGreater(after,before)
    def test_second_earlier_active_job_still_wins(self):
        before,after=expiry_transition([job('a','2026-12-01'),job('b','2026-11-15')],job('a','2027-01-01'))
        self.assertEqual(before,after)
if __name__=='__main__':unittest.main()
