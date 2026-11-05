"""Grounded grammar must not compete with word-internal surface-marker guesses."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('grammar_precedence',ROOT/'library/learn.py')
learn=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(learn)
NOW='2026-10-27T00:00:00+00:00'


def candidate(key, agent, event, patient, *, unknown=False):
    text=f'{agent}已{event} {patient}。'
    frames=[] if unknown else [{'kind':'expression','text':text,'form':'verbal','voice':'active',
        'slots':[{'role':role,'text':value} for role,value in [('agent',agent),('event',event),('patient',patient)]]}]
    draft={'round_id':'binding-only','candidates':[{'id':key,'label':'neutral','text':text,
           'analysis':{'spans':[]},'grammar':{'frames':frames}}]}
    return learn.bind_candidate_data(draft)['candidates'][0]


def legacy(item):
    return {key:value for key,value in item.items() if key!='grammar'}


class GrammarPrecedenceTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='sonder-grammar-precedence-');self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.db=str(self.root/'preference.db');self.file=self.root/'choice.json'
        clock=patch.object(learn,'_now_iso',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        self.training=candidate('train','设备','标记','3 个样本')
        self.merge=candidate('merge','系统','归并','8 组数据')
        self.clear=candidate('clear','系统','清除','8 组数据')

    def choose(self, pool, chosen, event):
        self.file.write_text(json.dumps({'round_id':event,'candidates':pool,'chosen':chosen,'author_owned':True},ensure_ascii=False))
        return learn.choice_file(self.db,'results','support',self.file)

    def templates(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute("SELECT feature,weight,picks,last_at FROM syntax_prefs WHERE feature LIKE '%:heuristic_template:%' ORDER BY feature").fetchall()

    def test_new_annotated_learning_has_equal_structure_for_same_form_with_different_words(self):
        self.choose([self.training],'train','training')
        rows=learn.rank_candidates(self.db,'results','support',[self.merge,self.clear])
        self.assertTrue(all(row['phrase_score']==0 for row in rows))
        self.assertEqual(rows[0]['grammar_score'],rows[1]['grammar_score'])
        self.assertEqual(rows[0]['base_syntax_score'],rows[1]['base_syntax_score'])
        self.assertEqual(rows[0]['score'],rows[1]['score'])
        self.assertFalse(self.templates(),'annotated learning must not create raw character templates')
        self.assertTrue(all(not row['heuristic_template_active'] for row in rows))

    def test_existing_surface_memory_is_preserved_but_not_used_by_annotated_candidates(self):
        self.choose([legacy(self.training)],'train','legacy-training')
        before=self.templates();self.assertTrue(before)
        for item in [self.clear,candidate('unknown','系统','清除','8 组数据',unknown=True)]:
            row=learn.rank_candidates(self.db,'results','support',[item])[0]
            self.assertFalse(any(':heuristic_template:' in feature['feature'] for feature in row['matched_syntax_features']))
            self.assertFalse(row['heuristic_template_active'])
        old=learn.rank_candidates(self.db,'results','support',[legacy(self.clear)])[0]
        self.assertTrue(any(':heuristic_template:' in feature['feature'] for feature in old['matched_syntax_features']))
        self.assertTrue(old['heuristic_template_active'])
        self.assertEqual(self.templates(),before)

    def test_explicit_grammar_abstention_does_not_become_negative_template_feedback(self):
        self.choose([legacy(self.training)],'train','legacy-training')
        before=self.templates()
        for i,selected in enumerate([self.merge,candidate('unknown','系统','归并','8 组数据',unknown=True)]):
            result=self.choose([selected,legacy(self.clear)],selected['id'],f'new-{i}')
            self.assertFalse(result.get('heuristic_template_active',True))
            self.assertEqual(self.templates(),before,'suppressed source is missing evidence, not rejection')
        positive=self.choose([legacy(self.clear),self.merge],'clear','legacy-positive')
        self.assertTrue(positive['heuristic_template_active'])
        self.assertGreater(self.templates()[0][1],before[0][1])

    def test_off_parser_and_parser_fallback_keep_distinct_source_diagnostics(self):
        learn.set_syntax_backend(self.db,'off')
        row=learn.rank_candidates(self.db,'results','support',[self.clear])[0]
        self.assertEqual(row['syntax_score'],0)
        self.assertFalse(row.get('heuristic_template_active',True))
        learn.set_syntax_backend(self.db,'stanza',str(self.root/'no-model'),'local')
        with patch.object(learn,'_stanza_syntax_features',side_effect=learn.SyntaxBackendUnavailable('missing model')):
            rows=learn.rank_candidates(self.db,'results','support',[self.merge,legacy(self.clear)])
        by_id={row['id']:row for row in rows}
        self.assertEqual(by_id['merge']['syntax_backend'],'heuristic-fallback')
        self.assertIn('missing model',by_id['merge']['syntax_fallback_reason'])
        self.assertFalse(by_id['merge']['heuristic_template_active'])
        self.assertTrue(by_id['clear']['heuristic_template_active'])
        parser_feature='syntax:v3:fixture:pos_seq:N V N PUNCT'
        with patch.object(learn,'_syntax_features',return_value=([parser_feature],'stanza')):
            features,backend,_,_=learn._candidate_syntax_features(self.merge,self.db)
            self.assertEqual(backend,'stanza');self.assertIn(parser_feature,features)

    def test_replay_preserves_historical_receipt_without_backfilling_policy_fields(self):
        first=self.choose([legacy(self.training)],'train','historic')
        old={key:value for key,value in first.items() if key!='heuristic_template_active'}
        with closing(sqlite3.connect(self.db)) as conn,conn:
            conn.execute('UPDATE choice_events SET result_json=?',(json.dumps(old,ensure_ascii=False),))
        with closing(sqlite3.connect(self.db)) as conn:before=list(conn.iterdump())
        replay=learn.choice_file(self.db,'results','support',self.file)
        self.assertEqual(replay,{**old,'replayed':True})
        self.assertNotIn('heuristic_template_active',replay)
        with closing(sqlite3.connect(self.db)) as conn:self.assertEqual(list(conn.iterdump()),before)


if __name__=='__main__':unittest.main()
