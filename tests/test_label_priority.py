"""Old edit labels must remain tie-breakers after long learning histories."""
from contextlib import closing
from copy import deepcopy
import importlib.util,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('text_first',ROOT/'library/learn.py')
learn=importlib.util.module_from_spec(spec);spec.loader.exec_module(learn)
NOW='2026-10-27T00:00:00+00:00'


class LabelPriorityTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='sonder-label-priority-');self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.db=str(self.root/'preference.db');self.file=self.root/'decision.json'
        clock=patch.object(learn,'_now_iso',return_value=NOW);clock.start();self.addCleanup(clock.stop)

    def choose(self,pool,chosen,event='one'):
        self.file.write_text(json.dumps({'candidates':pool,'chosen':chosen,'round_id':event,'author_owned':True},ensure_ascii=False))
        return learn.choice_file(self.db,'results','support',self.file)

    def dump(self):
        with closing(sqlite3.connect(self.db)) as c:return list(c.iterdump())

    def assert_text_scores(self,rows):
        for row in rows:
            expected=round(row['phrase_score']+learn.SYNTAX_SCALE*row['syntax_score'],6)
            self.assertAlmostEqual(row['score'],expected,places=5)

    def test_large_local_and_global_labels_cannot_reverse_learned_phrase_preference(self):
        learn.set_syntax_backend(self.db,'off')
        pool=[{'id':'accepted','label':'A','text':'我们测得薄膜电阻降低。'},
              {'id':'other','label':'Bold','text':'薄膜电阻降低这一结果由测量得到。'}]
        first=self.choose(pool,'accepted')
        learn.record(self.db,'results','support','Bold',learn.CEILING)
        learn.global_pref(self.db,'Bold',learn.CEILING)
        before=self.dump()
        rows=learn.rank_candidates(self.db,'results','support',pool[::-1])
        self.assertEqual(rows[0]['id'],'accepted')
        self.assertGreater(rows[0]['phrase_score'],rows[1]['phrase_score'])
        self.assert_text_scores(rows)
        renamed=learn.rank_candidates(self.db,'results','support',[{**c,'label':'Neutral'} for c in pool])
        self.assertEqual([(r['id'],r['score']) for r in rows],[(r['id'],r['score']) for r in renamed])
        self.assertEqual(self.dump(),before)
        self.assertEqual(learn.choice_file(self.db,'results','support',self.file),{**first,'replayed':True})
        self.assertEqual(self.dump(),before)

    def test_large_label_history_cannot_reverse_new_words_with_learned_structure(self):
        fixtures=json.loads((ROOT/'tests/fixtures/grammar-adjunct-placement.json').read_text())['candidates']
        self.choose(fixtures[:2],fixtures[0]['id'])
        held=deepcopy(fixtures[2:])
        held[0]['label']='Fresh';held[1]['label']='Bold'
        learn.record(self.db,'results','support','Bold',learn.CEILING)
        rows=learn.rank_candidates(self.db,'results','support',held[::-1])
        self.assertEqual(rows[0]['id'],held[0]['id'])
        self.assertGreater(rows[0]['grammar_score'],rows[1]['grammar_score'])
        self.assert_text_scores(rows)

    def test_cold_equal_text_scores_keep_legacy_decay_and_global_ties(self):
        # Same text makes substantive evidence identical, even after learning.
        pool=[{'id':'light','label':'Light','text':'样品保持稳定。'},
              {'id':'bold','label':'Bold','text':'样品保持稳定。'}]
        learn.record(self.db,'results','support','Light',1000)
        learn.record(self.db,'results','support','Bold',2000)
        rows=learn.rank_candidates(self.db,'results','support',pool)
        self.assertTrue(all(row['score']==0 for row in rows))
        self.assertEqual(rows[0]['id'],'bold')
        self.assertEqual([row['label_weight'] for row in rows],[2000,1000])
        with patch.object(learn,'_now_iso',return_value='2027-02-24T00:00:00+00:00'):
            learn.record(self.db,'results','support','Light',1000)
            updated=learn.rank_candidates(self.db,'results','support',pool)
            self.assertEqual(updated[0]['id'],'light')
            bias=learn.bias(self.db,'results','support',['Light','Bold'])
            self.assertEqual(updated[0]['label_weight'],bias['Light']['weight'])
            learn.global_pref(self.db,'Bold',5000)
            global_rows=learn.rank_candidates(self.db,'different','context',pool)
            self.assertEqual(global_rows[0]['id'],'bold')
            self.assertEqual(global_rows[0]['label_weight'],5000)
            self.assertTrue(all(row['score']==0 for row in global_rows))

    def test_exact_score_and_label_ties_preserve_all_candidates_in_input_order(self):
        pool=[{'id':'z','label':'Z','text':'甲方案有效。'},
              {'id':'a','label':'A','text':'乙方案有效。'},
              {'id':'m','label':'M','text':'丙方案有效。'}]
        for c in pool:learn.record(self.db,'results','support',c['label'])
        for order in (pool,pool[::-1]):
            rows=learn.rank_candidates(self.db,'results','support',order)
            self.assertEqual([r['id'] for r in rows],[c['id'] for c in order])
            self.assert_text_scores(rows)


if __name__=='__main__':unittest.main()
