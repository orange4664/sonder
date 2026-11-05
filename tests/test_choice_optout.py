"""Editorial approval and consent to learn are separate, durable decisions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing,ExitStack
from copy import deepcopy
import importlib.util,json,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('choice_optout',ROOT/'library/learn.py')
learn=importlib.util.module_from_spec(spec);spec.loader.exec_module(learn)
NOW='2026-10-27T00:00:00+00:00'


class ChoiceOptoutTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='sonder-choice-optout-');self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.db=str(self.root/'preference.db');self.file=self.root/'choice.json'
        self.data={'round_id':'editorial-only','chosen':'keep','author_owned':True,'learn_from_choice':False,
                   'candidates':[{'id':'keep','label':'Keep original','text':'实验记录将异常波形标记为待复核。'},
                                 {'id':'other','label':'Light','text':'实验记录把异常波形标记为待复核。'}]}
        clock=patch.object(learn,'_now_iso',return_value=NOW);clock.start();self.addCleanup(clock.stop)

    def submit(self,data=None):
        self.file.write_text(json.dumps(self.data if data is None else data,ensure_ascii=False))
        return learn.choice_file(self.db,'results','support',self.file)

    def rows(self,events=False):
        tables=['prefs','phrase_prefs','syntax_prefs','exemplars','seen']+(['choice_events'] if events else [])
        with closing(sqlite3.connect(self.db)) as c:
            return {table:c.execute('SELECT * FROM '+table+' ORDER BY 1,2').fetchall() for table in tables}

    def test_optout_records_identity_without_feature_work_or_learning(self):
        # Establish genuine earlier learning, then exclude the next decision.
        self.submit({**self.data,'round_id':'earlier','learn_from_choice':True})
        before=self.rows()
        with ExitStack() as stack:
            for function in ('_candidate_syntax_features','_phrase_features','_store_exemplar','_round_feature_feedback_tx'):
                stack.enter_context(patch.object(learn,function,side_effect=AssertionError('opt-out must skip '+function)))
            receipt=self.submit()
        self.assertFalse(receipt['feedback_applied']);self.assertFalse(receipt['author_exemplar'])
        self.assertTrue(receipt['idempotent']);self.assertFalse(receipt['replayed'])
        self.assertEqual(self.rows(),before)
        with closing(sqlite3.connect(self.db)) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM choice_events').fetchone()[0],2)
        self.assertNotIn('syntax_backend',receipt,'no parser invocation must not masquerade as a backend result')

    def test_fresh_optout_has_empty_learning_tables_and_requires_boolean_and_id(self):
        for value in (0,1,None,'false',[],{}):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'learn_from_choice'):
                self.submit({**self.data,'learn_from_choice':value})
            self.assertFalse(Path(self.db).exists())
        missing={k:v for k,v in self.data.items() if k!='round_id'}
        with self.assertRaisesRegex(ValueError,'round_id'):self.submit(missing)
        self.assertFalse(Path(self.db).exists())
        pending={k:v for k,v in self.data.items() if k!='chosen'}
        with self.assertRaisesRegex(ValueError,'chosen'):self.submit(pending)
        self.assertFalse(Path(self.db).exists())
        misplaced=deepcopy(self.data);misplaced.pop('learn_from_choice')
        misplaced['candidates'][0]['learn_from_choice']=False
        with self.assertRaisesRegex(ValueError,'file root'):self.submit(misplaced)
        self.assertFalse(Path(self.db).exists())
        receipt=self.submit();self.assertFalse(receipt['feedback_applied'])
        self.assertTrue(all(not rows for rows in self.rows().values()))

    def test_bind_preserves_optout_and_wrong_id_never_opens_store(self):
        draft=deepcopy(self.data)
        for candidate in draft['candidates']:
            candidate.update(analysis={'spans':[]},grammar={'frames':[]})
        try:bound=learn.bind_candidate_data(draft)
        except ValueError as error:self.fail('binder must preserve explicit learning intent: '+str(error))
        self.assertIs(bound['learn_from_choice'],False)
        self.file.write_text(json.dumps(bound,ensure_ascii=False))
        with self.assertRaisesRegex(ValueError,'round_id'):
            learn.choice_file(self.db,'results','support',self.file,expected_round_id='different')
        self.assertFalse(Path(self.db).exists())
        receipt=learn.choice_file(self.db,'results','support',self.file,expected_round_id=bound['round_id'])
        self.assertFalse(receipt['feedback_applied'])

    def test_optout_replay_concurrency_and_conflicting_intent(self):
        self.file.write_text(json.dumps(self.data,ensure_ascii=False))
        with ThreadPoolExecutor(max_workers=6) as pool:
            receipts=list(pool.map(lambda _:learn.choice_file(self.db,'results','support',self.file),range(6)))
        self.assertTrue(all(receipt.get('feedback_applied') is False for receipt in receipts))
        self.assertEqual(sum(not r['replayed'] for r in receipts),1)
        before=self.rows(events=True)
        with self.assertRaisesRegex(ValueError,'round_id'):self.submit({**self.data,'learn_from_choice':True})
        self.assertEqual(self.rows(events=True),before)
        original=next(r for r in receipts if not r['replayed'])
        self.assertEqual(self.submit(),{**original,'replayed':True})
        self.assertEqual(self.rows(events=True),before)
        self.assertTrue(all(not rows for rows in self.rows().values()))

    def test_default_and_explicit_true_keep_old_identity_and_later_choices_still_learn(self):
        candidates=learn._validated_candidates(self.data['candidates'])
        legacy_hash=learn._choice_payload_hash('results','support',candidates,'keep',True)
        first=self.submit({k:v for k,v in self.data.items() if k!='learn_from_choice'})
        self.assertTrue(first.get('feedback_applied',False))
        before=self.rows(events=True)
        self.assertEqual(self.submit({**self.data,'learn_from_choice':True}),{**first,'replayed':True})
        self.assertEqual(self.rows(events=True),before)
        with closing(sqlite3.connect(self.db)) as c:self.assertEqual(c.execute('SELECT payload_hash FROM choice_events').fetchone()[0],legacy_hash)
        with self.assertRaisesRegex(ValueError,'round_id'):self.submit()
        skipped=self.submit({**self.data,'round_id':'skip-later'});self.assertFalse(skipped['feedback_applied'])
        baseline=self.rows()
        learned=self.submit({**self.data,'round_id':'separate-new-choice','learn_from_choice':True})
        self.assertTrue(learned['feedback_applied']);self.assertNotEqual(self.rows(),baseline)

    def test_receipt_failure_rolls_back_editorial_completion(self):
        self.submit({**self.data,'round_id':'earlier','learn_from_choice':True})
        before=self.rows(events=True)
        original=learn._store_choice_receipt
        def fail_after_insert(*args):
            original(*args)
            raise sqlite3.OperationalError('simulated receipt failure')
        with patch.object(learn,'_store_choice_receipt',side_effect=fail_after_insert):
            with self.assertRaisesRegex(sqlite3.OperationalError,'receipt failure'):self.submit()
        self.assertEqual(self.rows(events=True),before)
        receipt=self.submit();self.assertFalse(receipt['feedback_applied'])

    def test_cli_restart_replays_optout_and_old_receipt_is_not_backfilled(self):
        first=self.submit();self.assertFalse(first.get('feedback_applied',True))
        before=self.rows(events=True)
        p=subprocess.run([sys.executable,'-B','-I',str(ROOT/'library/learn.py'),'choice-file',self.db,'results','support',str(self.file)],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(json.loads(p.stdout),{**first,'replayed':True})
        self.assertEqual(self.rows(events=True),before)
        normal={**self.data,'round_id':'historical','learn_from_choice':True};receipt=self.submit(normal)
        old={k:v for k,v in receipt.items() if k!='feedback_applied'}
        with closing(sqlite3.connect(self.db)) as c,c:
            c.execute('UPDATE choice_events SET result_json=? WHERE round_id=?',(json.dumps(old,ensure_ascii=False),'historical'))
        before=self.rows(events=True)
        self.assertEqual(self.submit(normal),{**old,'replayed':True})
        self.assertEqual(self.rows(events=True),before)


if __name__=='__main__':unittest.main()
