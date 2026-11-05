"""New decisions use serialized write time, never stale pre-extraction time."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('feedback_clock',ROOT/'library/learn.py')
learn=importlib.util.module_from_spec(spec);spec.loader.exec_module(learn)
T0='2026-10-28T00:00:00+00:00'
T1='2026-10-28T00:00:10+00:00'
T2='2026-10-28T00:00:20+00:00'


class FeedbackClockTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='sonder-feedback-clock-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.base={'chosen':'a','author_owned':True,'candidates':[
            {'id':'a','label':'Other','text':'样本保持稳定。'},
            {'id':'b','label':'Alternative','text':'观察到样本状态未变。'}]}
        self.clock=T0
        p=patch.object(learn,'_now_iso',side_effect=lambda:self.clock);p.start();self.addCleanup(p.stop)

    def payload(self,name,optout=False):
        data={**deepcopy(self.base),'round_id':name}
        if optout:data['learn_from_choice']=False
        path=self.root/(name+'.json');path.write_text(json.dumps(data,ensure_ascii=False));return path

    def choose(self,db,path):
        return learn.choice_file(str(db),'methods','detail',path)

    def rows(self,db,table):
        with sqlite3.connect(db) as c:
            c.row_factory=sqlite3.Row
            return [dict(row) for row in c.execute('SELECT * FROM '+table+' ORDER BY rowid')]

    def test_late_finishing_earlier_request_matches_serial_write_order(self):
        slow,fast=self.payload('slow'),self.payload('fast')
        db=self.root/'concurrent.db';serial=self.root/'serial.db'
        entered=threading.Event();release=threading.Event();gated=False;errors=[]
        original=learn._candidate_syntax_features
        def extraction(*args,**kwargs):
            nonlocal gated
            if threading.current_thread().name=='slow-choice' and not gated:
                gated=True;entered.set()
                if not release.wait(10):raise RuntimeError('test release timed out')
            return original(*args,**kwargs)
        def submit_slow():
            try:self.choose(db,slow)
            except BaseException as error:errors.append(error)
        with patch.object(learn,'_candidate_syntax_features',side_effect=extraction):
            worker=threading.Thread(target=submit_slow,name='slow-choice');worker.start()
            try:
                self.assertTrue(entered.wait(10))
                self.clock=T1;self.choose(db,fast)
                self.clock=T2
            finally:
                release.set();worker.join(10)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
        self.clock=T1;self.choose(serial,fast)
        self.clock=T2;self.choose(serial,slow)
        for table in ['prefs','phrase_prefs','syntax_prefs','exemplars','choice_events']:
            with self.subTest(table=table):self.assertEqual(self.rows(db,table),self.rows(serial,table))

    def test_normal_and_optout_capture_time_after_writer_lock(self):
        original=learn._open;owner=self
        class Connection:
            def __init__(self,conn):self.conn=conn
            def __getattr__(self,key):return getattr(self.conn,key)
            def __enter__(self):self.conn.__enter__();return self
            def __exit__(self,*args):return self.conn.__exit__(*args)
            def execute(self,sql,*args):
                result=self.conn.execute(sql,*args)
                if sql=='BEGIN IMMEDIATE':owner.clock=T1
                return result
        for optout in [False,True]:
            with self.subTest(optout=optout):
                self.clock=T0;db=self.root/('locked-'+str(optout)+'.db');path=self.payload('locked-'+str(optout),optout)
                with patch.object(learn,'_open',side_effect=lambda db:Connection(original(db))):self.choose(db,path)
                self.assertEqual(self.rows(db,'choice_events')[0]['created'],T1)
                if not optout:
                    for table in ['prefs','phrase_prefs','syntax_prefs','exemplars']:
                        self.assertTrue(all(x['last_at']==T1 for x in self.rows(db,table)))

    def test_author_example_shares_decision_timestamp_even_if_clock_advances(self):
        db=self.root/'shared.db';path=self.payload('shared');original=learn._store_exemplar
        def delayed(*args,**kwargs):
            self.clock=T2
            return original(*args,**kwargs)
        self.clock=T1
        with patch.object(learn,'_store_exemplar',side_effect=delayed):self.choose(db,path)
        self.assertEqual(self.rows(db,'choice_events')[0]['created'],T1)
        exemplar=self.rows(db,'exemplars')[0]
        self.assertEqual(exemplar['created'],T1)
        self.assertEqual(exemplar['last_at'],T1)

    def test_standalone_store_holds_write_transaction_before_timestamp(self):
        db=self.root/'legacy.db';original=learn._store_exemplar
        def store(conn,*args,**kwargs):
            self.assertTrue(conn.in_transaction,'legacy store must lock before sampling feedback time')
            return original(conn,*args,**kwargs)
        with patch.object(learn,'_store_exemplar',side_effect=store):
            learn.store(str(db),'author','测量结果稳定。')
        self.assertEqual(self.rows(db,'exemplars')[0]['last_at'],T0)


if __name__=='__main__':unittest.main()
