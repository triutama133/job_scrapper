"""Tes sinkron inkremental upload_to_supabase (klien Supabase palsu; tidak menyentuh jaringan/database).
Jalankan: python -m unittest tests.test_upload_incremental -v"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import upload_to_supabase as up


class FakeTable:
    def __init__(self, client):
        self.client = client

    def upsert(self, batch, on_conflict=None):
        self.batch = batch
        return self

    def execute(self):
        c = self.client
        c.calls += 1
        if c.fail_on_call and c.calls == c.fail_on_call:
            raise RuntimeError("boom")
        c.sent.extend(self.batch)
        return self


class FakeClient:
    def __init__(self):
        self.sent, self.calls, self.fail_on_call = [], 0, None

    def table(self, name):
        return FakeTable(self)


def make_jobs(n):
    return [{"job_title": f"Job {i}", "company": "Co", "platform": "X", "job_url": f"https://x.test/{i}",
             "description": "d" * 1000, "job_desk": ["a"], "requirements": ["b"], "is_active": True,
             "status_reason": "Active", "category": "Others / General", "salary": None,
             "last_checked": "2026-10-31T08:00:00"} for i in range(n)]


class IncrementalUploadTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.file = os.path.join(self.dir, "jobs.json")
        self.state = os.path.join(self.dir, "state.json")
        self.jobs = make_jobs(250)
        self.write_jobs()
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"] = "http://fake", "fake"
        self.client = FakeClient()
        up.create_client = lambda url, key: self.client

    def write_jobs(self):
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(self.jobs, f)

    def run_upload(self, **kw):
        self.client.sent, self.client.calls = [], 0
        ok = up.upload_jobs_to_supabase(filepath=self.file, state_file=self.state, **kw)
        return ok, [r["job_url"] for r in self.client.sent]

    def test_first_run_is_full_then_nothing_changes(self):
        ok, sent = self.run_upload()
        self.assertTrue(ok); self.assertEqual(len(sent), 250)
        self.assertIsNotNone(up.load_state(self.state)["last_full_sync"])
        ok, sent = self.run_upload()
        self.assertTrue(ok); self.assertEqual(sent, [])

    def test_only_new_and_changed_rows_are_sent(self):
        self.run_upload()
        self.jobs[5]["salary"] = "IDR 10,000,000 - 15,000,000"
        self.jobs.append(make_jobs(251)[250]); self.write_jobs()
        ok, sent = self.run_upload()
        self.assertTrue(ok); self.assertEqual(sorted(sent), ["https://x.test/250", "https://x.test/5"])

    def test_last_checked_same_day_is_ignored_next_day_is_sent(self):
        self.run_upload()
        self.jobs[3]["last_checked"] = "2026-10-31T21:30:00"; self.write_jobs()
        self.assertEqual(self.run_upload()[1], [])
        self.jobs[3]["last_checked"] = "2026-11-01T01:00:00"; self.write_jobs()
        self.assertEqual(self.run_upload()[1], ["https://x.test/3"])

    def test_inactive_description_is_trimmed_in_payload_only(self):
        self.jobs[0]["is_active"] = False; self.write_jobs()
        self.run_upload()
        sent = {r["job_url"]: r for r in self.client.sent}
        self.assertEqual(len(sent["https://x.test/0"]["description"]), 300)
        self.assertEqual(len(sent["https://x.test/1"]["description"]), 1000)
        self.assertEqual(len(self.jobs[0]["description"]), 1000)   # file lokal tetap penuh

    def test_full_flag_and_stale_state_resend_everything(self):
        self.run_upload()
        self.assertEqual(len(self.run_upload(full=True)[1]), 250)
        state = up.load_state(self.state)
        state["last_full_sync"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        with open(self.state, "w") as f: json.dump(state, f)
        self.assertEqual(len(self.run_upload()[1]), 250)

    def test_failed_batch_is_retried_but_succeeded_batches_are_not(self):
        self.run_upload()
        for j in self.jobs[:150]: j["salary"] = "IDR 1,000,000 - 2,000,000"
        self.write_jobs()
        self.client.fail_on_call = 2
        self.client.sent, self.client.calls = [], 0
        ok = up.upload_jobs_to_supabase(filepath=self.file, state_file=self.state)
        self.assertFalse(ok)
        self.client.fail_on_call = None
        ok, sent = self.run_upload()
        self.assertTrue(ok); self.assertEqual(len(sent), 50)       # batch 1 (100 baris) sudah tercatat; sisa 50 diulang
        self.assertEqual(self.run_upload()[1], [])

    def test_urls_mode_sends_exactly_those_and_records_state(self):
        self.run_upload()
        self.jobs[7]["salary"] = "IDR 5,000,000 - 6,000,000"; self.write_jobs()
        ok, sent = self.run_upload(urls=["https://x.test/7", "https://x.test/8"])
        self.assertTrue(ok); self.assertEqual(sorted(sent), ["https://x.test/7", "https://x.test/8"])
        self.assertEqual(self.run_upload()[1], [])                  # job 7 sudah tercatat, tidak dikirim lagi


if __name__ == "__main__":
    unittest.main()
