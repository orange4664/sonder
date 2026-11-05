"""Long accepted prose must not lose lexical evidence by sorted-prefix quota."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('phrase_coverage',ROOT/'library/learn.py')
learn=importlib.util.module_from_spec(spec);spec.loader.exec_module(learn)
LONG=(ROOT/'tests/fixtures/long-method-paragraph.txt').read_text().strip()
IMPORTANT={'word:quality','word:assurance','word:qa','cjk3:追溯性'}
NOW='2026-10-28T00:00:00+00:00'


class PhraseCoverageTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='sonder-phrase-coverage-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.db=str(self.root/'preference.db');self.path=self.root/'choice.json'
        p=patch.object(learn,'_now_iso',return_value=NOW);p.start();self.addCleanup(p.stop)
        learn.set_syntax_backend(self.db,'off')

    def choose(self,text,round_id,alternative=None):
        candidates=[{'id':'keep','label':'Keep original','text':text}]
        if alternative:candidates.append({'id':'alt','label':'Other','text':alternative})
        data={'round_id':round_id,'chosen':'keep','author_owned':True,'candidates':candidates}
        self.path.write_text(json.dumps(data,ensure_ascii=False))
        return learn.choice_file(self.db,'methods','detail',self.path)

    def rows(self):
        with sqlite3.connect(self.db) as c:
            return {r[0]:r[1:] for r in c.execute('SELECT feature,weight,picks,last_at FROM phrase_prefs')}

    def dump(self):
        with sqlite3.connect(self.db) as c:return list(c.iterdump())

    def test_complete_long_mixed_language_features_survive(self):
        features=learn._phrase_features(LONG)
        self.assertGreater(len(features),256)
        self.assertTrue(IMPORTANT.issubset(features))
        self.assertEqual(features,sorted(set(features)))

    def test_long_acceptance_keeps_shared_positive_evidence(self):
        # Mechanical feature feedback probe, not a set of equivalent rewrites.
        short='质量保证（quality assurance，QA）记录具有可追溯性。'
        self.choose(short,'seed')
        self.assertTrue(IMPORTANT.issubset(self.rows()))
        self.choose(LONG,'long',alternative=short)
        for feature in IMPORTANT:
            with self.subTest(feature=feature):
                self.assertEqual(self.rows()[feature],(2000,2,NOW),
                                 'a phrase present in accepted text must not be omitted or penalized')

    def test_rank_reports_full_query_count_and_retrieves_learned_words(self):
        receipt=self.choose(LONG,'full')
        self.assertGreater(receipt['features'],256)
        self.assertTrue(IMPORTANT.issubset(self.rows()))
        candidates=[{'id':'probe','label':'Never chosen','text':'quality assurance QA 可追溯性。'}]
        state=self.dump()
        row=learn.rank_candidates(self.db,'methods','detail',candidates)[0]
        self.assertEqual(row.get('phrase_feature_count'),len(learn._phrase_features(candidates[0]['text'])))
        self.assertEqual(row['label_weight'],0)
        self.assertTrue(IMPORTANT.issubset({x['feature'] for x in row['matched_features']}))
        self.assertGreater(row['score'],0)
        self.assertEqual(learn._rank_summary([row])[0]['phrase_feature_count'],row['phrase_feature_count'])
        self.assertEqual(self.dump(),state)

    def test_old_truncated_receipt_replays_without_backfilling(self):
        complete=learn._phrase_features
        with patch.object(learn,'_phrase_features',side_effect=lambda text:complete(text)[:256]):
            old=self.choose(LONG,'historical-truncated')
        self.assertEqual(old['features'],256)
        state=self.dump()
        self.assertEqual(learn.choice_file(self.db,'methods','detail',self.path),{**old,'replayed':True})
        self.assertEqual(self.dump(),state)
        self.assertFalse(IMPORTANT.intersection(self.rows()))
        new=self.choose(LONG,'new-approval')
        self.assertGreater(new['features'],256)
        self.assertTrue(IMPORTANT.issubset(self.rows()))
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM choice_events').fetchone()[0],2)


if __name__=='__main__':unittest.main()
