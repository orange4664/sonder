"""A failed parser supplies missing evidence, not an author's rejection."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('fallback_learn',ROOT/'library/learn.py')
learn=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(learn)


def document(rows):
    tokens=[SimpleNamespace(text=text,words=[SimpleNamespace(id=i,text=text,upos=upos,head=head,deprel=relation)])
            for i,(text,upos,head,relation) in enumerate(rows,1)]
    return SimpleNamespace(sentences=[SimpleNamespace(tokens=tokens)])


class SyntaxFallbackTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='sonder-fallback-')
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.db=str(self.root/'preference.db')
        clock=patch.object(learn,'_now_iso',return_value='2026-10-27T00:00:00+00:00')
        clock.start();self.addCleanup(clock.stop)
        self.failed=set()
        self.texts={'active':'团队测量样品。','passive':'样品由团队测量。'}
        self.documents={
            'active':document([('团队','NOUN',2,'nsubj'),('测量','VERB',0,'root'),('样品','NOUN',2,'obj'),('。','PUNCT',2,'punct')]),
            'passive':document([('样品','NOUN',4,'nsubj:pass'),('由','ADP',3,'case'),('团队','NOUN',4,'obl:agent'),('测量','VERB',0,'root'),('。','PUNCT',4,'punct')]),
        }
        drafts=[]
        for key,text in self.texts.items():
            drafts.append({'id':key,'label':'Probe','text':text,
                'analysis':{'spans':[{'text':text,'move':'method','polarity':'affirmative','certainty':'asserted'}]},
                'grammar':{'frames':[{'kind':'expression','text':text,'form':'verbal','voice':key,
                    'slots':[{'role':role,'text':fragment} for role,fragment in
                             [('agent','团队'),('event','测量'),('patient','样品')]]}]}})
        self.candidates=learn.bind_candidate_data({'round_id':'binding','candidates':drafts})['candidates']
        learn.set_syntax_backend(self.db,'stanza',str(self.root),'controlled-fixture')
        self.pipeline=lambda text:self.parser(text)
        self.pipeline._sonder_model_fingerprint='a'*64

    def parser(self,text):
        key=next(k for k,v in self.texts.items() if v==text)
        if key in self.failed:raise RuntimeError(f'fixture parse failure for {key}')
        return self.documents[key]

    def choose(self,key,event):
        file=self.root/f'{event}.json'
        file.write_text(json.dumps({'round_id':event,'candidates':self.candidates,'chosen':key,'author_owned':True},ensure_ascii=False))
        return learn.choice_file(self.db,'methods','support',file)

    def rows(self,prefix):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute('SELECT * FROM syntax_prefs WHERE feature LIKE ? ORDER BY feature',(prefix+'%',)).fetchall()

    def test_missing_model_cli_explains_fallback(self):
        missing=self.root/'missing-model'
        learn.set_syntax_backend(self.db,'stanza',str(missing),'not-installed')
        file=self.root/'rank.json';file.write_text(json.dumps({'candidates':self.candidates},ensure_ascii=False))
        p=subprocess.run([sys.executable,'-B','-I',str(ROOT/'library/learn.py'),'rank-file',self.db,'methods','support',str(file)],capture_output=True,text=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stderr)
        for row in json.loads(p.stdout):
            self.assertEqual(row['syntax_backend'],'heuristic-fallback')
            self.assertEqual(row.get('syntax_requested_backend'),'stanza')
            self.assertIn('does not exist',row.get('syntax_fallback_reason',''))

    def test_selected_parse_failure_preserves_parser_weights_but_learns_other_layers(self):
        with patch.object(learn,'_stanza_pipeline',return_value=self.pipeline):
            for i in range(3):self.choose('passive',f'prior-{i}')
            before=self.rows('syntax:v3:stanza-')
            self.failed={'active'}
            result=self.choose('active','partial-failure')
            self.assertEqual(self.rows('syntax:v3:stanza-'),before)
            self.assertIs(result.get('base_syntax_negative_feedback'),False)
            self.assertTrue(result['analysis_negative_feedback'])
            self.assertTrue(result['grammar_negative_feedback'])
            with closing(sqlite3.connect(self.db)) as conn:
                self.assertEqual(conn.execute('SELECT rewards FROM exemplars WHERE text=?',(self.texts['active'],)).fetchone(),(1,))
                self.assertGreater(conn.execute('SELECT COUNT(*) FROM phrase_prefs WHERE picks>0').fetchone()[0],0)
                # Independent, fully known host annotations still support a
                # genuine voice preference despite the base parser failure.
                self.assertEqual(conn.execute('SELECT weight FROM syntax_prefs WHERE feature=?',
                    ('syntax:grammar-v1:writing-model:voice:passive',)).fetchone(),(4250,))
                snapshot=list(conn.iterdump())
            self.assertEqual(result['syntax_requested_backend'],'stanza')
            self.assertIn('fixture parse failure for active',result['syntax_fallback_reason'])
            self.assertEqual(set(result['syntax_fallbacks']),{'active'})
            with patch.object(learn,'_stanza_pipeline',side_effect=AssertionError('replay must not parse')):
                self.assertEqual(self.choose('active','partial-failure'),{**result,'replayed':True})
            with closing(sqlite3.connect(self.db)) as conn:self.assertEqual(list(conn.iterdump()),snapshot)

    def test_unselected_parse_failure_does_not_penalize_fallback_features(self):
        with patch.object(learn,'_stanza_pipeline',return_value=self.pipeline):
            self.failed={'active','passive'}
            for i in range(3):self.choose('passive',f'fallback-prior-{i}')
            before=self.rows('syntax:v3:heuristic:')
            self.assertTrue(before)
            self.failed={'passive'}
            result=self.choose('active','other-candidate-failed')
            self.assertEqual(self.rows('syntax:v3:heuristic:'),before)
            self.assertIs(result.get('base_syntax_negative_feedback'),False)
            self.assertEqual(result['syntax_backend'],'stanza')
            self.assertIsNone(result['syntax_fallback_reason'])
            self.assertEqual(set(result['syntax_fallbacks']),{'passive'})

    def test_consistent_fallback_remains_learnable_and_diagnostics_do_not_leak(self):
        with patch.object(learn,'_stanza_pipeline',return_value=self.pipeline):
            self.failed={'active','passive'}
            result=self.choose('active','all-fallback')
            self.assertIs(result.get('base_syntax_negative_feedback'),True)
            self.assertTrue(self.rows('syntax:v3:heuristic:'))
            rows=learn.rank_candidates(self.db,'methods','support',self.candidates)
            self.assertTrue(all(row['syntax_fallback_reason'] for row in rows))
            self.failed=set()
            rows=learn.rank_candidates(self.db,'methods','support',self.candidates)
            self.assertTrue(all(row['syntax_backend']=='stanza' and row['syntax_fallback_reason'] is None for row in rows))
            learn.set_syntax_backend(self.db,'off')
            disabled=learn.rank_candidates(self.db,'methods','support',self.candidates)
            self.assertTrue(all(row['syntax_requested_backend']=='off' and row['syntax_fallback_reason'] is None and row['syntax_score']==0 for row in disabled))


if __name__=='__main__':unittest.main()
