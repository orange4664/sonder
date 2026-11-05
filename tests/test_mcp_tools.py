"""Optional MCP adapter policy and engine parity without installing the SDK."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import inspect
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
ADAPTER=ROOT/'library/mcp_tools.py'


class WorkspaceToolTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(ADAPTER.is_file(),'optional workspace adapter is missing')
        spec=importlib.util.spec_from_file_location('mcp_adapter_tests',ADAPTER)
        self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
        tmp=tempfile.TemporaryDirectory(prefix='sonder-mcp-tools-');self.addCleanup(tmp.cleanup)
        self.parent=Path(tmp.name).resolve();self.root=self.parent/'revision';self.root.mkdir()
        self.tools=self.module.WorkspaceTools(self.root)
        self.db=self.root/'preference.db'
        self.text='测得 $x$ = "a"，路径 C:\\tmp。\r\n下一行。'.replace('\\r\\n','\r\n')

    def prepare(self,chosen=False,round_id='round-1'):
        candidates=self.root/'candidate-inputs';candidates.mkdir(exist_ok=True)
        data={'round_id':round_id,'candidates':[
            {'id':'own','label':'Other','text':self.text,'analysis':{'spans':[]},'grammar':{'frames':[]}},
            {'id':'alt','label':'Alternative','text':'测量结果仍待复核。','analysis':{'spans':[]},'grammar':{'frames':[]}}]}
        draft=candidates/'draft.json';draft.write_text(json.dumps(data,ensure_ascii=False))
        result=self.tools.bind('candidate-inputs/draft.json','candidate-inputs/bound.json')
        path=Path(result['output'])
        if chosen:
            data=json.loads(path.read_text());data.update(chosen='own',author_owned=True)
            path.write_text(json.dumps(data,ensure_ascii=False))
        return path

    def dump(self):
        with sqlite3.connect(self.db) as c:return list(c.iterdump())

    def test_choice_receipt_export_stays_in_receipt_directory_and_uses_core(self):
        file = self.prepare(chosen=True)
        folder = self.root / 'choice-receipts'
        folder.mkdir()
        output = folder / 'round-1.json'
        self.assertIn('receipt_file', inspect.signature(self.tools.record_choice).parameters,
                      'optional MCP receipt export parameter is missing')
        engine = self.tools.engine
        self.assertTrue(callable(getattr(engine, 'choice_file_export', None)))
        with patch.object(engine, 'choice_file_export', wraps=engine.choice_file_export) as core:
            result = self.tools.record_choice('results', 'support', str(file), 'round-1',
                                              receipt_file='choice-receipts/round-1.json')
        self.assertEqual(core.call_count, 1)
        self.assertEqual(result['receipt_file'], str(output))
        self.assertFalse(result['receipt_file_reused'])
        before = self.dump(), output.read_bytes(), output.stat().st_mtime_ns
        retry = self.tools.record_choice('results', 'support', str(file), 'round-1',
                                         receipt_file=str(output))
        self.assertTrue(retry['replayed'])
        self.assertTrue(retry['receipt_file_reused'])
        self.assertEqual((self.dump(), output.read_bytes(), output.stat().st_mtime_ns), before)
        self.assertNotIn('receipt_file', self.tools.record_choice('results', 'support', str(file), 'round-1'))
        self.assertFalse(json.loads(output.read_text())['result']['replayed'])

    def test_choice_receipt_export_rejects_unsafe_paths_and_wrong_id_without_feedback(self):
        file = self.prepare(chosen=True)
        folder = self.root / 'choice-receipts'
        folder.mkdir()
        outside = self.parent / 'outside'
        outside.mkdir()
        (folder / 'escape').symlink_to(outside, target_is_directory=True)
        self.assertIn('receipt_file', inspect.signature(self.tools.record_choice).parameters)
        for output in ['', False, 'receipt.json', 'preference.db', 'candidate-inputs/bound.json',
                       'choice-receipts', '../outside/receipt.json', str(outside / 'receipt.json'),
                       'choice-receipts/escape/receipt.json', 'choice-receipts/absent/receipt.json']:
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.tools.record_choice('results', 'support', str(file), 'round-1', receipt_file=output)
            self.assertFalse(self.db.exists())
        with self.assertRaisesRegex(ValueError, 'round_id'):
            self.tools.record_choice('results', 'support', str(file), 'wrong-id',
                                     receipt_file='choice-receipts/receipt.json')
        self.assertFalse(self.db.exists())
        self.assertFalse((folder / 'receipt.json').exists())

    def test_startup_status_does_not_create_database_and_reports_bound_workspace(self):
        status=self.tools.status()
        self.assertEqual(status['workspace'],str(self.root))
        self.assertEqual(status['database'],str(self.db))
        self.assertEqual(status['engine_path'],str(ROOT/'library/learn.py'))
        self.assertIn('choice_id_guard',status['engine_info']['capabilities'])
        self.assertEqual(status['engine_info']['scoring_version'],2)
        self.assertIn('independent_analysis_scoring',status['engine_info']['capabilities'])
        self.assertFalse(self.db.exists())
        with self.assertRaises(ValueError):self.module.WorkspaceTools(self.parent/'absent')
        self.assertFalse((self.parent/'absent').exists())

    def test_learning_policy_uses_core_and_preserves_explicit_legacy(self):
        self.assertTrue(callable(getattr(self.tools, 'learning_policy', None)),
                        'MCP learning-policy adapter is missing')
        engine = self.tools.engine
        with patch.object(engine, 'learning_policy', wraps=engine.learning_policy) as core:
            enabled = self.tools.learning_policy('ensure-adaptive')
        core.assert_called_once_with(str(self.db), 'ensure-adaptive')
        self.assertEqual(enabled['policy'], 'adaptive-v3')
        self.assertEqual(enabled['scoring_version'], 5)
        self.assertTrue(enabled['adaptive_initialized'])
        before = self.dump()
        again = self.tools.learning_policy('ensure-adaptive')
        self.assertFalse(again['changed'])
        self.assertEqual(self.dump(), before)
        self.tools.learning_policy('legacy')
        legacy = self.dump()
        self.assertEqual(self.tools.learning_policy('ensure-adaptive')['policy'], 'legacy')
        self.assertEqual(self.dump(), legacy)

    def test_learning_policy_rejects_invalid_action_before_store(self):
        self.assertTrue(callable(getattr(self.tools, 'learning_policy', None)))
        for action in ['', 'auto', True, 3, []]:
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.tools.learning_policy(action)
            self.assertFalse(self.db.exists())

    def test_binding_literal_text_scope_and_non_overwrite(self):
        file=self.prepare()
        self.assertEqual(json.loads(file.read_text())['candidates'][0]['text'],self.text)
        before=file.read_bytes()
        self.assertTrue(self.tools.bind('candidate-inputs/draft.json','candidate-inputs/bound.json')['reused'])
        self.assertEqual(file.read_bytes(),before);self.assertFalse(self.db.exists())
        with self.assertRaises(ValueError):self.tools.bind('candidate-inputs/draft.json','working/output.json')
        with self.assertRaises(ValueError):self.tools.bind('candidate-inputs/draft.json','candidate-inputs')
        self.assertFalse((self.root/'working').exists())
        draft=self.root/'candidate-inputs/draft.json';data=json.loads(draft.read_text());data['candidates'][0]['text']='不同正文。';draft.write_text(json.dumps(data,ensure_ascii=False))
        with self.assertRaisesRegex(ValueError,'already exists'):self.tools.bind(str(draft),str(file))
        self.assertEqual(file.read_bytes(),before)

    def test_pending_missing_or_wrong_id_never_learns(self):
        file=self.prepare()
        with self.assertRaisesRegex(ValueError,'chosen'):self.tools.record_choice('results','support',str(file),'round-1')
        self.assertFalse(self.db.exists())
        data=json.loads(file.read_text());data['chosen']='own';data['author_owned']=True;file.write_text(json.dumps(data,ensure_ascii=False))
        for expected in ('wrong','',None,True):
            with self.subTest(expected=expected),self.assertRaises(ValueError):self.tools.record_choice('results','support',str(file),expected)
            self.assertFalse(self.db.exists())
        data.pop('round_id');file.write_text(json.dumps(data,ensure_ascii=False))
        with self.assertRaisesRegex(ValueError,'round_id'):self.tools.record_choice('results','support',str(file),'round-1')
        self.assertFalse(self.db.exists())

    def test_cli_mcp_restart_replay_and_author_exemplar_share_one_engine_contract(self):
        file=self.prepare(chosen=True)
        first=self.tools.record_choice('results','support',str(file),'round-1')
        self.assertTrue(first['idempotent']);self.assertFalse(first['replayed'])
        before=self.dump()
        fresh=self.module.WorkspaceTools(self.root)
        self.assertNotEqual(fresh.status()['instance_id'],self.tools.status()['instance_id'])
        self.assertEqual(fresh.record_choice('results','support',str(file),'round-1'),{**first,'replayed':True})
        p=subprocess.run([sys.executable,'-B','-I',str(ROOT/'library/learn.py'),'choice-file',str(self.db),'results','support',str(file)],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(json.loads(p.stdout),{**first,'replayed':True})
        self.assertEqual(self.dump(),before)
        query=self.root/'sentence.txt';query.write_bytes(self.text.encode())
        self.assertEqual(self.tools.nearest(str(query),3)['exemplars'][0]['text'],self.text)
        ranked=self.tools.rank('results','support',str(file))['candidates'];self.assertEqual(ranked[0]['id'],'own')
        self.assertTrue(all(row['scoring_version']==2 and 'analysis_score' in row for row in ranked))
        data=json.loads(file.read_text());data['chosen']='alt';file.write_text(json.dumps(data,ensure_ascii=False))
        with self.assertRaisesRegex(ValueError,'round_id'):self.tools.record_choice('results','support',str(file),'round-1')
        self.assertEqual(self.dump(),before)

    def test_input_database_and_settings_symlinks_cannot_leave_workspace(self):
        outside=self.parent/'outside.txt';outside.write_text('private sentinel')
        (self.root/'escape.txt').symlink_to(outside)
        for path in ('../outside.txt',str(outside),'escape.txt'):
            with self.subTest(path=path),self.assertRaises(ValueError):self.tools.nearest(path)
        self.assertFalse(self.db.exists())
        self.db.symlink_to(outside)
        with self.assertRaises(ValueError):self.tools.status()
        self.db.unlink()
        nested=self.root/'nested';nested.mkdir()
        self.db.symlink_to(nested/'preference.db')
        with self.assertRaises(ValueError):self.tools.status()
        self.db.unlink()
        (self.root/'.similarity_backend').symlink_to(outside)
        with self.assertRaises(ValueError):self.tools.status()
        self.assertEqual(outside.read_text(),'private sentinel')

    def test_serial_calls_and_cli_system_exit_become_request_errors(self):
        file=self.prepare()
        active=0;maximum=0;counter=threading.Lock()
        def ranking(*args):
            nonlocal active,maximum
            with counter:active+=1;maximum=max(maximum,active)
            time.sleep(0.01)
            with counter:active-=1
            return []
        with patch.object(self.tools.engine,'rank_candidates',side_effect=ranking),ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _:self.tools.rank('results','support',str(file)),range(8)))
        self.assertEqual(maximum,1)
        query=self.root/'sentence.txt';query.write_text(self.text)
        with patch.object(self.tools.engine,'nearest',side_effect=SystemExit('install optional matcher')):
            with self.assertRaisesRegex(ValueError,'optional matcher'):self.tools.nearest(str(query))
        self.assertEqual(self.tools.status()['workspace'],str(self.root))

    def test_mixed_engine_capability_rejected_before_store_access(self):
        with patch.object(self.module,'_load_engine',return_value=SimpleNamespace(engine_info=lambda:{})):
            with self.assertRaises(ValueError):self.module.WorkspaceTools(self.root)
        self.assertFalse(self.db.exists())

    def test_saved_markdown_mode_reaches_rank_summary_choice_and_restart(self):
        plain = '测量结果不支持该机制，但不能排除其他解释。'
        marked = plain.replace('不支持', '**不**支持')
        path = self.root/'markdown.json'
        data = {'round_id': 'seed-md', 'chosen': 'plain', 'author_owned': True,
                'candidates': [{'id': 'plain', 'label': 'Equal', 'text': plain},
                               {'id': 'md', 'label': 'Equal', 'text': marked}]}
        path.write_text(json.dumps(data, ensure_ascii=False))
        self.tools.record_choice('results', 'support', str(path), 'seed-md')
        data.update(round_id='explicit-md', chosen='md', text_format='markdown')
        path.write_text(json.dumps(data, ensure_ascii=False))
        for summary in (False, True):
            with self.subTest(summary=summary):
                rows = self.tools.rank('results', 'support', str(path), summary=summary)['candidates']
                self.assertEqual(rows[0]['score'], rows[1]['score'])
                self.assertEqual(rows[0]['phrase_feature_count'], rows[1]['phrase_feature_count'])
                if summary:
                    self.assertEqual(rows[0]['matched_feature_counts'], rows[1]['matched_feature_counts'])
                else:
                    self.assertEqual(rows[0]['matched_features'], rows[1]['matched_features'])
                self.assertTrue(all('text_format' not in row for row in rows))
        first = self.tools.record_choice('results', 'support', str(path), 'explicit-md')
        self.assertEqual(first.get('text_format'), 'markdown')
        before = self.dump()
        fresh = self.module.WorkspaceTools(self.root)
        self.assertEqual(fresh.record_choice('results', 'support', str(path), 'explicit-md'), {**first, 'replayed': True})
        self.assertEqual(self.dump(), before)

    def test_server_help_and_missing_sdk_do_not_require_or_touch_a_store(self):
        server=ROOT/'scripts/mcp_server.py';self.assertTrue(server.is_file())
        help_result=subprocess.run([sys.executable,'-B','-I','-S',str(server),'--help'],capture_output=True,text=True)
        self.assertEqual(help_result.returncode,0,help_result.stderr)
        missing=subprocess.run([sys.executable,'-B','-I','-S',str(server),'--workspace',str(self.root)],capture_output=True,text=True)
        self.assertNotEqual(missing.returncode,0);self.assertIn('optional MCP',missing.stderr)
        self.assertEqual(missing.stdout,'');self.assertNotIn('Traceback',missing.stderr)
        self.assertFalse(self.db.exists())

    def test_nearest_query_mode_is_forwarded_without_changing_raw_exemplars(self):
        self.tools.engine.store(str(self.db), 'author', 'pre**condition**', text_format='markdown')
        query = self.root/'markdown-query.txt'; query.write_text('pre*condition*')
        row = self.tools.nearest(str(query), text_format='markdown')['exemplars'][0]
        self.assertEqual((row['text'], row['sim'], row.get('text_format')), ('pre**condition**', 1.0, 'markdown'))
        legacy = self.tools.nearest(str(query))['exemplars'][0]
        self.assertLess(legacy['sim'], 1.0)
        for value in ('tex', False, [], ''):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'text_format'):
                self.tools.nearest(str(self.root/'missing-query'), text_format=value)


if __name__=='__main__':unittest.main()
