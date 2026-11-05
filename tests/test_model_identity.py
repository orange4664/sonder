"""Content identity must prevent manual model tags from merging parser memories."""
from contextlib import closing
from copy import deepcopy
import json
import shutil
import sqlite3
import unittest
from unittest.mock import patch

import test_model_cache as fixtures

learn=fixtures.learn


class ModelIdentityTests(fixtures.ModelFixture,unittest.TestCase):
    def rank(self):
        pool=deepcopy(self.candidates)
        for c in pool:c['label']='Unseen label'
        return learn.rank_candidates(self.db,'methods','support',pool)

    def replace_weights(self):
        replacement=self.models/'replacement.bin'
        replacement.write_bytes(b'other');replacement.replace(self.weights)

    def annotate(self):
        draft={'round_id':'bind','candidates':[]}
        for c in self.candidates:
            draft['candidates'].append({**c,
                'analysis':{'spans':[{'text':c['text'],'move':'method','polarity':'affirmative','certainty':'asserted'}]},
                'grammar':{'frames':[{'kind':'expression','text':c['text'],'form':'verbal','voice':c['id'],
                    'slots':[{'role':r,'text':t} for r,t in [('agent','团队'),('event','测量'),('patient','样品')]]}]}})
        self.candidates=learn.bind_candidate_data(draft)['candidates']

    def test_changed_bytes_same_tag_isolate_parser_but_preserve_other_memory(self):
        self.annotate()
        receipt=self.choose('active','first')
        identity=receipt.get('syntax_model_fingerprint','')
        self.assertRegex(identity,r'^[0-9a-f]{64}$')
        with closing(sqlite3.connect(self.db)) as c:
            old=c.execute("SELECT * FROM syntax_prefs WHERE feature LIKE 'syntax:v3:stanza-%' ORDER BY feature").fetchall()
        self.replace_weights()
        ranked=self.rank()
        self.assertNotEqual(ranked[0]['syntax_model_fingerprint'],identity)
        self.assertTrue(all(r['syntax_model_version']=='old' for r in ranked))
        self.assertTrue(all(not any(f['feature'].startswith('syntax:v3:stanza-') for f in r['matched_syntax_features']) for r in ranked))
        self.assertGreater(next(r for r in ranked if r['id']=='active')['phrase_score'],0)
        self.assertGreater(next(r for r in ranked if r['id']=='active')['grammar_score'],0)
        self.assertEqual(learn.list_exemplars(self.db,'author')[0]['text'],self.candidates[0]['text'])
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute("SELECT * FROM syntax_prefs WHERE feature LIKE 'syntax:v3:stanza-%' ORDER BY feature").fetchall(),old)

    def test_identical_content_moved_and_retagged_keeps_identity_and_weights(self):
        self.choose('active','first')
        before=self.rank()
        self.assertRegex(before[0].get('syntax_model_fingerprint',''),r'^[0-9a-f]{64}$')
        moved=self.root/'moved-models';shutil.copytree(self.models,moved)
        (moved/'resources.json').write_text('{"unrelated_manifest_metadata":true}')
        learn.set_syntax_backend(self.db,'stanza',moved,'renamed-human-tag')
        after=self.rank()
        self.assertEqual([r['syntax_model_fingerprint'] for r in before],[r['syntax_model_fingerprint'] for r in after])
        self.assertEqual([r['score'] for r in before],[r['score'] for r in after])
        self.assertEqual(before[0]['matched_syntax_features'],after[0]['matched_syntax_features'])
        self.assertEqual(after[0]['syntax_model_version'],'renamed-human-tag')

    def test_provider_version_changes_identity_even_when_files_are_same(self):
        before=self.rank()[0].get('syntax_model_fingerprint','')
        self.assertRegex(before,r'^[0-9a-f]{64}$')
        self.fake_stanza.__version__='fixture-v2'
        after=self.rank()[0]['syntax_model_fingerprint']
        self.assertNotEqual(before,after)

    def test_loading_drift_and_unavailable_identity_fall_back(self):
        def change_once():
            self.on_load=None;self.replace_weights()
        self.on_load=change_once
        rows=self.rank()
        self.assertTrue(all(r['syntax_backend']=='heuristic-fallback' for r in rows[:1]))
        self.assertIn('changed',rows[0]['syntax_fallback_reason'])
        learn._SYNTAX_PARSER_CACHE.clear()
        with patch.object(learn,'_stanza_model_config',return_value={}):
            row=self.rank()[0]
        self.assertEqual(row['syntax_backend'],'heuristic-fallback')
        self.assertIn('model',row['syntax_fallback_reason'])

    def test_same_tag_mixed_fingerprints_do_not_reject_other_source(self):
        self.annotate()
        for i in range(3):receipt=self.choose('passive',f'prior-{i}')
        fingerprint=receipt.get('syntax_model_fingerprint','')
        self.assertRegex(fingerprint,r'^[0-9a-f]{64}$')
        key=f'syntax:v3:stanza-id1-{fingerprint}:dep_seq:nsubj:pass,case,obl:agent,root'
        def change_once():
            self.on_parse=None;self.replace_weights()
        self.on_parse=change_once
        result=self.choose('active','mixed-content')
        self.assertEqual(result['syntax_model_version'],'old')
        self.assertEqual(result['syntax_model_fingerprint'],fingerprint)
        self.assertEqual(len(set(result['syntax_model_fingerprints'].values())),2)
        self.assertFalse(result['base_syntax_negative_feedback'])
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT weight FROM syntax_prefs WHERE feature=?',(key,)).fetchone(),(4500,))
            self.assertEqual(c.execute('SELECT weight FROM syntax_prefs WHERE feature=?',('syntax:grammar-v1:writing-model:voice:passive',)).fetchone(),(4250,))
            snapshot=list(c.iterdump())
        with patch.object(learn,'_stanza_pipeline',side_effect=AssertionError('replay must not parse')):
            self.assertEqual(self.choose('active','mixed-content'),{**result,'replayed':True})
        with closing(sqlite3.connect(self.db)) as c:self.assertEqual(list(c.iterdump()),snapshot)

    def test_legacy_parser_rows_are_retained_but_not_assumed_current(self):
        learn.list_exemplars(self.db)
        key='syntax:v3:stanza-old:dep_shape:root:ROOT>V=1'
        with closing(sqlite3.connect(self.db)) as c,c:
            c.execute('INSERT INTO syntax_prefs VALUES (?,?,?,?,?)',('methods::support',key,9000,6,learn._now_iso()))
        rows=self.rank()
        self.assertTrue(all(r['syntax_score']==0 for r in rows))
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT weight FROM syntax_prefs WHERE feature=?',(key,)).fetchone(),(9000,))

    def test_unchanged_cache_does_not_hash_files_for_each_sentence(self):
        self.assertTrue(hasattr(learn,'_hash_stanza_model_file'))
        with patch.object(learn,'_hash_stanza_model_file',wraps=learn._hash_stanza_model_file) as hashing:
            self.rank();initial=hashing.call_count
            self.assertGreater(initial,0)
            self.rank();self.rank()
            self.assertEqual(hashing.call_count,initial)


if __name__=='__main__':unittest.main()
