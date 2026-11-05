"""Custom context tuples stay isolated without guessing old ambiguous history."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import itertools
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('context_keys_engine', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec); spec.loader.exec_module(learn)
FIXTURE = json.loads((ROOT / 'tests/fixtures/coextensive-grammar.json').read_text())
A = ('methods::detail', 'support')
B = ('methods', 'detail::support')
BOUNDARY_A = ('a:', 'b')
BOUNDARY_B = ('a', ':b')
NOW = '2026-10-29T14:00:00+00:00'


class ContextKeyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='sonder-context-keys-')
        self.addCleanup(tmp.cleanup); self.root = Path(tmp.name)
        self.db = str(self.root / 'preference.db')
        self.pool = learn.bind_candidate_data(FIXTURE['training'])['candidates']
        self.query = learn.bind_candidate_data(FIXTURE['query'])['candidates']
        clock = patch.object(learn, '_now_iso', return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)

    def file(self, context=A, event='one', chosen='active', **extra):
        data = {'round_id': event, 'learning_context': dict(zip(('section','role'),context)),
                'candidates': deepcopy(self.pool), 'chosen': chosen, 'author_owned': True, **extra}
        path = self.root / (event + '.json'); path.write_text(json.dumps(data, ensure_ascii=False))
        return path

    def choose(self, context=A, event='one', chosen='active', **extra):
        return learn.choice_file(self.db, *context, self.file(context, event, chosen, **extra))

    def rank(self, context):
        return learn.rank_candidates(self.db, *context, self.query)

    def dump(self):
        with closing(sqlite3.connect(self.db)) as conn: return list(conn.iterdump())

    def legacy(self, context=A):
        # Emulate the exact old key path in a portable unit test. Actual old
        # engine subprocesses are exercised separately by the SDK/release gate.
        learn.learning_policy(self.db, 'ensure-adaptive')
        with patch.object(learn, '_context', side_effect=lambda s,r: f'{s}::{r}'):
            receipt = self.choose(context, 'legacy')
        with sqlite3.connect(self.db) as conn: conn.execute('PRAGMA user_version=8')
        return receipt

    def test_ordinary_keys_stay_exact_and_ambiguous_pairs_are_injective(self):
        for section,role in [('methods','detail'), ('实验:装置','说明'), ('a:b','c:d'), ('','')]:
            self.assertEqual(learn._context(section,role), f'{section}::{role}')
        values = ['', 'a', ':', '::', 'a:', ':a', 'a::b', 'x:y', '方法', '细节::补充',
                  '\\u003a', '"quoted"', '\nline', '@sonder-context-v2:["a","b"]']
        mapped = {}
        for pair in itertools.product(values, repeat=2):
            key = learn._context(*pair)
            self.assertNotIn(key, mapped, (pair, mapped.get(key), key))
            mapped[key] = pair
            if '::' not in key:
                self.assertTrue(key.startswith(learn._CONTEXT_KEY_PREFIX))
                self.assertEqual(learn._legacy_context_alias(key), '::'.join(pair))
        self.assertNotEqual(learn._context(*A), learn._context(*B))
        self.assertNotEqual(learn._context(*BOUNDARY_A), learn._context(*BOUNDARY_B))
        self.assertNotIn('__GLOBAL__', mapped)

    def test_fake_encoding_prefix_in_normal_context_remains_an_ordinary_key(self):
        pair = ('@sonder-context-v2:["a","b"]', 'detail')
        key = learn._context(*pair)
        self.assertEqual(key, '::'.join(pair))
        self.assertIsNone(learn._legacy_context_alias(key))
        learn.record(self.db, *pair, 'Keep', 1000)
        self.assertEqual(learn.bias(self.db, *pair, ['Keep'])['Keep']['weight'], 1000)

    def test_actual_choices_in_colliding_pairs_learn_opposite_preferences_independently(self):
        learn.learning_policy(self.db, 'ensure-adaptive')
        for left,right in [(A,B),(BOUNDARY_A,BOUNDARY_B)]:
            event = 'embedded' if left == A else 'boundary'
            self.assertTrue(all(row['score'] == 0 for row in self.rank(right)))
            self.choose(left,event+'-left','active')
            self.assertGreater(self.rank(left)[0]['score'], 0)
            self.assertTrue(all(row['score'] == 0 for row in self.rank(right)))
            self.choose(right,event+'-right','passive')
            self.assertEqual(self.rank(left)[0]['id'],'active')
            self.assertEqual(self.rank(right)[0]['id'],'passive')
        self.assertTrue(all(row['score']==0 for row in self.rank(('methods','detail'))))

    def test_legacy_label_signal_global_and_reshow_keep_their_semantics(self):
        learn.record(self.db,*A,'Local',1000)
        self.assertEqual(learn.bias(self.db,*B,['Local'])['Local']['weight'],0)
        learn.record(self.db,*B,'Local',2000)
        learn.signal(self.db,*A,'Local',-400)
        self.assertEqual(learn.bias(self.db,*A,['Local'])['Local']['weight'],600)
        self.assertEqual(learn.bias(self.db,*B,['Local'])['Local']['weight'],2000)
        learn.global_pref(self.db,'Global',3000)
        self.assertEqual(learn.bias(self.db,*A,['Global'])['Global']['weight'],3000)
        self.assertEqual(learn.bias(self.db,*B,['Global'])['Global']['weight'],3000)
        output = io.StringIO()
        with redirect_stdout(output):
            learn.reshow(self.db)
        self.assertEqual({row['context'] for row in json.loads(output.getvalue())},
                         {learn._context(*A), learn._context(*B)})

    def test_old_ambiguous_history_rejects_rank_learning_record_signal_and_bias_without_mutation(self):
        self.legacy()
        # The format upgrade preserves the complete old row dump.
        before = self.dump()
        with patch.object(learn,'_candidate_syntax_features',side_effect=AssertionError('legacy guard must precede parsing')):
            for context in [A,B]:
                for action in [lambda: self.rank(context), lambda: self.choose(context,'new'),
                               lambda: learn.record(self.db,*context,'New'),
                               lambda: learn.signal(self.db,*context,'Old'),
                               lambda: learn.bias(self.db,*context,['Old'])]:
                    with self.assertRaisesRegex(ValueError,'ambiguous legacy context'):
                        action()
                    self.assertEqual(self.dump(),before)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],9)

    def test_guard_covers_each_context_table_even_without_other_history(self):
        learn.learning_policy(self.db,'ensure-adaptive')
        legacy_key='::'.join(A)
        inserts={
            'prefs': ('context,opt,weight,picks,last_at',(legacy_key,'Old',1000,1,NOW)),
            'seen': ('context,opt,at',(legacy_key,'Old',NOW)),
            'phrase_prefs': ('context,feature,weight,picks,last_at',(legacy_key,'phrase',1000,1,NOW)),
            'syntax_prefs': ('context,feature,weight,picks,last_at',(legacy_key,'syntax',1000,1,NOW)),
            'adaptive_weights': ('context,channel,feature,weight,last_at',(legacy_key,'profile','phrase|old',1.0,NOW)),
            'adaptive_legacy': ('context,feature,weight,last_at',(legacy_key,'phrase|old',1000,NOW)),
            'adaptive_legacy_mass': ('context,raw_mass',(legacy_key,1000)),
            'adaptive_opposition': ('context,feature,value,last_at',(legacy_key,'phrase|old',1.0,NOW)),
            'adaptive_relative': ('context,feature,value,last_at',(legacy_key,next(iter(learn._relations.KEY_GROUP)),1.0,NOW)),
        }
        self.assertEqual(set(inserts),set(learn._CONTEXT_TABLES))
        for table,(columns,values) in inserts.items():
            with self.subTest(table=table):
                with sqlite3.connect(self.db) as conn:
                    conn.execute('INSERT INTO '+table+' ('+columns+') VALUES ('+','.join('?' for _ in values)+')',values)
                before=self.dump()
                with self.assertRaisesRegex(ValueError,'ambiguous legacy context'): self.rank(A)
                self.assertEqual(self.dump(),before)
                with sqlite3.connect(self.db) as conn: conn.execute('DELETE FROM '+table+' WHERE context=?',(legacy_key,))

    def test_old_receipt_and_export_replay_before_guard_or_feature_work(self):
        receipt=self.legacy();before=self.dump();path=self.root/'legacy.json';out=self.root/'receipt.json'
        with patch.object(learn,'_candidate_syntax_features',side_effect=AssertionError('replay must not parse')):
            self.assertEqual(learn.choice_file(self.db,*A,path),{**receipt,'replayed':True})
            exported=learn.choice_file_export(self.db,*A,path,out)
        self.assertTrue(exported['replayed']);self.assertEqual(json.loads(out.read_text())['result'],receipt)
        self.assertEqual(self.dump(),before)
        changed=json.loads(path.read_text());changed['learning_context']=dict(zip(('section','role'),B));path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError,'round_id'): learn.choice_file(self.db,*B,path)
        self.assertEqual(self.dump(),before)

    def test_optout_stays_available_without_touching_ambiguous_learning_history(self):
        self.legacy();before=self.dump()
        with patch.object(learn,'_candidate_syntax_features',side_effect=AssertionError('opt-out must not parse')):
            receipt=self.choose(B,'editorial',learn_from_choice=False)
        self.assertFalse(receipt['feedback_applied']);self.assertFalse(receipt['author_exemplar'])
        with sqlite3.connect(self.db) as conn: conn.execute("DELETE FROM choice_events WHERE round_id='editorial'")
        self.assertEqual(self.dump(),before)

    def test_new_encoded_receipt_replays_and_policy_switches_preserve_keys(self):
        learn.learning_policy(self.db,'ensure-adaptive');receipt=self.choose();before=self.dump()
        self.assertEqual(self.choose(),{**receipt,'replayed':True});self.assertEqual(self.dump(),before)
        for policy in ['legacy','adaptive-v1','adaptive-v2','adaptive-v3']:
            learn.learning_policy(self.db,policy)
            self.assertGreater(self.rank(A)[0]['score'],0)
            self.assertTrue(all(row['score']==0 for row in self.rank(B)))

    def test_guard_rechecks_external_legacy_history_after_extraction_before_commit_and_read(self):
        for operation in ['rank','choice']:
            with self.subTest(operation=operation):
                if Path(self.db).exists(): Path(self.db).unlink()
                learn.learning_policy(self.db,'ensure-adaptive')
                original=learn._candidate_syntax_features;injected=[]
                def parse(*args,**kwargs):
                    result=original(*args,**kwargs)
                    if not injected:
                        with sqlite3.connect(self.db) as conn:
                            conn.execute('INSERT INTO prefs VALUES (?,?,?,?,?)',('::'.join(A),'Old',1000,1,NOW))
                        injected.append(self.dump())
                    return result
                with patch.object(learn,'_candidate_syntax_features',side_effect=parse):
                    with self.assertRaisesRegex(ValueError,'ambiguous legacy context'):
                        self.rank(A) if operation=='rank' else self.choose()
                self.assertEqual(self.dump(),injected[0])

    def test_read_guard_and_weights_share_one_snapshot(self):
        learn.learning_policy(self.db,'ensure-adaptive');self.choose();before=self.rank(A)
        with sqlite3.connect(self.db) as conn: conn.execute('PRAGMA journal_mode=WAL')
        original=learn._require_resolved_context;written=[]
        def guard(conn,key):
            original(conn,key)
            if conn.in_transaction and not written:
                with sqlite3.connect(self.db) as writer:
                    writer.execute('INSERT INTO prefs VALUES (?,?,?,?,?)',('::'.join(A),'Old',1000,1,NOW))
                written.append(True)
        with patch.object(learn,'_require_resolved_context',side_effect=guard): during=self.rank(A)
        self.assertTrue(written);self.assertEqual(during,before)
        with self.assertRaisesRegex(ValueError,'ambiguous legacy context'): self.rank(A)

    def test_concurrent_context_feedback_has_separate_rows_and_one_receipt_per_id(self):
        learn.learning_policy(self.db,'ensure-adaptive')
        jobs=[(A,'A-'+str(i),'active') for i in range(4)]+[(B,'B-'+str(i),'passive') for i in range(4)]
        # Save first, then workers use immutable input files.
        paths=[(ctx,self.file(ctx,event,chosen)) for ctx,event,chosen in jobs]
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts=list(pool.map(lambda row:learn.choice_file(self.db,*row[0],row[1]),paths*2))
        self.assertEqual(sum(not r['replayed'] for r in receipts),8)
        self.assertEqual(self.rank(A)[0]['id'],'active');self.assertEqual(self.rank(B)[0]['id'],'passive')
        with sqlite3.connect(self.db) as conn:
            self.assertEqual({x[0] for x in conn.execute('SELECT DISTINCT context FROM adaptive_weights')}, {learn._context(*A),learn._context(*B)})

    def test_prune_uses_full_opaque_keys_and_does_not_delete_other_contexts(self):
        learn.record(self.db,*A,'Old',1);learn.record(self.db,*B,'Recent',1000)
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE prefs SET last_at=? WHERE context=?',('2020-01-01T00:00:00+00:00',learn._context(*A)))
        learn.prune(self.db)
        self.assertEqual(learn.bias(self.db,*A,['Old'])['Old']['weight'],0)
        self.assertEqual(learn.bias(self.db,*B,['Recent'])['Recent']['weight'],1000)

    def test_cli_reproduced_alias_stays_cold_for_other_tuple(self):
        p=self.file(A,'cli')
        base=[sys.executable,'-B','-I',str(ROOT/'library/learn.py')]
        chosen=subprocess.run(base+['choice-file',self.db,*A,str(p)],capture_output=True,text=True,timeout=20)
        self.assertEqual(chosen.returncode,0,chosen.stderr)
        query=self.file(B,'query');data=json.loads(query.read_text());data.pop('chosen');data.pop('author_owned');query.write_text(json.dumps(data))
        ranked=subprocess.run(base+['rank-file',self.db,*B,str(query),'--summary'],capture_output=True,text=True,timeout=20)
        self.assertEqual(ranked.returncode,0,ranked.stderr)
        self.assertTrue(all(row['score']==0 and row['label_weight']==0 for row in json.loads(ranked.stdout)))


if __name__=='__main__': unittest.main()
