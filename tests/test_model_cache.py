"""Cached optional models must retain the provenance of the parser actually used."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('cache_learn',ROOT/'library/learn.py')
learn=importlib.util.module_from_spec(spec);spec.loader.exec_module(learn)


def fixture_config(directory):
    return {f'{p}_model_path':str(Path(directory)/'weights.bin')
            for p in ('tokenize','pos','lemma','depparse')}


class ModelFixture:
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='sonder-cache-')
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.db=str(self.root/'preference.db')
        self.models=self.root/'models';self.models.mkdir()
        self.weights=self.models/'weights.bin';self.weights.write_bytes(b'first')
        (self.models/'resources.json').write_text('{}')
        self.loads=[];self.on_parse=None;self.on_load=None
        owner=self

        class Pipeline:
            def __init__(self,**kwargs):
                self.config=fixture_config(kwargs['dir'])
                self.generation=Path(self.config['tokenize_model_path']).read_bytes()
                owner.loads.append(self)
                if owner.on_load is not None: owner.on_load()

            def __call__(self,text):
                if owner.on_parse is not None: owner.on_parse()
                rows=([('团队','NOUN',2,'nsubj'),('测量','VERB',0,'root'),('样品','NOUN',2,'obj')]
                      if text=='团队测量样品。' else
                      [('样品','NOUN',4,'nsubj:pass'),('由','ADP',3,'case'),('团队','NOUN',4,'obl:agent'),('测量','VERB',0,'root')])
                return SimpleNamespace(sentences=[SimpleNamespace(tokens=[
                    SimpleNamespace(text=t,words=[SimpleNamespace(id=i,text=t,upos=p,head=h,deprel=d)])
                    for i,(t,p,h,d) in enumerate(rows,1)])])

        fake=SimpleNamespace(Pipeline=Pipeline,DownloadMethod=SimpleNamespace(NONE='no-download'),__version__='fixture-v1')
        self.fake_stanza=fake
        patched=patch.dict(sys.modules,{'stanza':fake});patched.start();self.addCleanup(patched.stop)
        resolver=patch.object(learn,'_stanza_model_config',create=True,
            side_effect=fixture_config)
        resolver.start();self.addCleanup(resolver.stop)
        clock=patch.object(learn,'_now_iso',return_value='2026-10-27T00:00:00+00:00');clock.start();self.addCleanup(clock.stop)
        learn._SYNTAX_PARSER_CACHE.clear()
        learn.set_syntax_backend(self.db,'stanza',self.models,'old')
        self.candidates=[{'id':'active','label':'Probe','text':'团队测量样品。'},
                         {'id':'passive','label':'Probe','text':'样品由团队测量。'}]

    def choose(self,chosen,event):
        file=self.root/f'{event}.json'
        file.write_text(json.dumps(dict(round_id=event,candidates=self.candidates,chosen=chosen,author_owned=True),ensure_ascii=False))
        return learn.choice_file(self.db,'methods','support',file)


class ModelCacheTests(ModelFixture,unittest.TestCase):
    def test_cache_observes_external_version_and_other_workspace_database(self):
        first=learn._stanza_pipeline(self.db)
        (self.root/'.syntax_model_version').write_text('external')
        second=learn._stanza_pipeline(self.db)
        self.assertIsNot(first,second)
        learn.set_syntax_backend(str(self.root/'another.db'),'stanza',self.models,'third')
        self.assertIsNot(second,learn._stanza_pipeline(self.db))
        self.assertEqual(len(self.loads),3)

    def test_cache_reuses_unchanged_but_invalidates_artifact_and_resources(self):
        first=learn._stanza_pipeline(self.db)
        self.assertIs(first,learn._stanza_pipeline(self.db))
        (self.models/'notes.txt').write_text('unrelated')
        self.assertIs(first,learn._stanza_pipeline(self.db))
        replacement=self.models/'replacement.bin';replacement.write_bytes(b'other');replacement.replace(self.weights)
        second=learn._stanza_pipeline(self.db)
        self.assertIsNot(first,second)
        self.assertEqual(second.generation,b'other')
        (self.models/'resources.json').write_text('{"changed":true}')
        third=learn._stanza_pipeline(self.db)
        self.assertIsNot(second,third)
        self.weights.unlink()
        diagnostics={}
        _,backend=learn._syntax_features(self.candidates[0]['text'],self.db,diagnostics)
        self.assertEqual(backend,'heuristic-fallback')
        self.assertIn('weights.bin',diagnostics['fallback_reason'])

    def test_namespace_and_rank_keep_loaded_version_when_config_changes_during_parse(self):
        def change_once():
            self.on_parse=None
            (self.root/'.syntax_model_version').write_text('new')
        self.on_parse=change_once
        first=learn.rank_candidates(self.db,'methods','support',[self.candidates[0]])[0]
        self.assertEqual(first['syntax_model_version'],'old')
        # The next call can adopt the new configuration, while this extraction
        # must never retroactively claim the new version parsed its text.
        diagnostics={}
        parser=self.loads[0]
        with patch.object(learn,'_stanza_pipeline',return_value=parser):
            features,_=learn._syntax_features(self.candidates[0]['text'],self.db,diagnostics)
        self.assertTrue(any(f":stanza-id1-{first['syntax_model_fingerprint']}:" in f for f in features))
        second=learn.rank_candidates(self.db,'methods','support',[self.candidates[0]])[0]
        self.assertEqual(second['syntax_model_version'],'new')
        self.assertEqual(first['syntax_model_fingerprint'],second['syntax_model_fingerprint'])

    def test_mixed_versions_do_not_supply_base_rejection_evidence(self):
        for i in range(3):receipt=self.choose('passive',f'prior-{i}')
        key=f"syntax:v3:stanza-id1-{receipt['syntax_model_fingerprint']}:dep_seq:nsubj:pass,case,obl:agent,root"
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT weight FROM syntax_prefs WHERE feature=?',(key,)).fetchone(),(4500,))
        def change_once():
            self.on_parse=None
            (self.root/'.syntax_model_version').write_text('new')
            replacement=self.models/'replacement.bin';replacement.write_bytes(b'other');replacement.replace(self.weights)
        self.on_parse=change_once
        result=self.choose('active','mixed-version')
        self.assertEqual(result['syntax_model_version'],'old')
        self.assertIs(result['base_syntax_negative_feedback'],False)
        self.assertEqual(result['syntax_fallbacks'],{})
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT weight FROM syntax_prefs WHERE feature=?',(key,)).fetchone(),(4500,))
            self.assertEqual(conn.execute('SELECT rewards FROM exemplars WHERE text=?',(self.candidates[0]['text'],)).fetchone(),(1,))
            before=list(conn.iterdump())
        with patch.object(learn,'_stanza_pipeline',side_effect=AssertionError('replay must not load')):
            self.assertEqual(self.choose('active','mixed-version'),{**result,'replayed':True})
        with closing(sqlite3.connect(self.db)) as conn:self.assertEqual(list(conn.iterdump()),before)


if __name__=='__main__':unittest.main()
