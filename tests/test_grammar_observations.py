"""Observation identity preserves partial evidence without hiding conflict."""
from contextlib import closing
from copy import deepcopy
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('observation_learn',ROOT/'library/learn.py')
learn=importlib.util.module_from_spec(spec);spec.loader.exec_module(learn)


def candidate():
    text='研究者测量了信号。团队分析了误差。'
    def expression(fragment,parts):
        start=text.index(fragment)
        return dict(kind='expression',start=start,end=start+len(fragment),text=fragment,
                    form='verbal',voice='active',slots_complete=True,
                    slots=[dict(role=role,start=text.index(part),end=text.index(part)+len(part),text=part)
                           for role,part in parts])
    return dict(id='one',label='Keep',text=text,grammar=dict(version=6,source='writing-model',
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),frames=[
            expression('研究者测量了信号',[('agent','研究者'),('event','测量'),('patient','信号')]),
            expression('团队分析了误差',[('agent','团队'),('event','分析'),('patient','误差')])]))


def add_duplicate(item,index=0):
    frame=deepcopy(item['grammar']['frames'][index])
    assert item['text'][frame['end']]=='。'
    frame['end']+=1;frame['text']=item['text'][frame['start']:frame['end']]
    item['grammar']['frames'].append(frame)
    return frame


class GrammarObservationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='sonder-grammar-observations-')
        self.addCleanup(tmp.cleanup);self.root=Path(tmp.name);self.db=str(self.root/'preference.db')
        clock=patch.object(learn,'_now_iso',return_value='2026-10-27T15:00:00+00:00')
        clock.start();self.addCleanup(clock.stop)
    def rank(self,item):return learn.rank_candidates(self.db,'methods','detail',[item])[0]
    def choose(self,item,event='first'):
        p=self.root/'choice.json';p.write_text(json.dumps(dict(round_id=event,candidates=[item],chosen=item['id'],author_owned=True),ensure_ascii=False))
        return learn.choice_file(self.db,'methods','detail',p)
    def dump(self):
        with closing(sqlite3.connect(self.db)) as c:return list(c.iterdump())
    def test_partial_expression_duplicates_preserve_full_scores_and_features(self):
        self.choose(candidate())
        for field,value in [('form','unknown'),('voice','unknown'),('slots_complete',False)]:
            item=candidate();item['grammar']['frames'][0][field]=value
            before=self.rank(item);add_duplicate(item);after=self.rank(item)
            with self.subTest(field=field):
                self.assertEqual(before['score'],after['score'])
                self.assertEqual(before['grammar_scores_by_family'],after['grammar_scores_by_family'])
                self.assertEqual(before['matched_grammar_features'],after['matched_grammar_features'])
    def test_conflicting_expression_fields_abstain_individually(self):
        item=candidate();duplicate=add_duplicate(item);duplicate['form']='nominalized'
        row=self.rank(item);features=learn._grammar_features(row['grammar'])
        self.assertNotIn(learn.GRAMMAR_PREFIX+'form:nominalized',features)
        self.assertIn(learn.GRAMMAR_PREFIX+'voice:active',features)
        self.assertEqual(learn._grammar_unknown_counts(row['grammar'])['form'],1)
        self.assertEqual(row['grammar_status'],'partial')
    def test_role_conflict_preserves_form_voice_without_arbitrary_slot_shape(self):
        item=candidate();duplicate=add_duplicate(item)
        duplicate['slots'][0]['role']='patient';duplicate['slots'][2]['role']='agent'
        row=self.rank(item);features=learn._grammar_features(row['grammar'])
        self.assertNotIn(learn.GRAMMAR_PREFIX+'expression_slots:verbal:patient>event>agent',features)
        self.assertIn(learn.GRAMMAR_PREFIX+'form:verbal',features)
        self.assertIn(learn.GRAMMAR_PREFIX+'voice:active',features)
        self.assertEqual(learn._grammar_unknown_counts(row['grammar'])['expression_slots'],1)
        self.assertEqual(row['grammar_status'],'partial')
    def test_masking_duplicate_and_distinct_observations_never_raises_score(self):
        self.choose(candidate());base=candidate();add_duplicate(base)
        for field in ['form','voice','slots_complete']:
            scores={}
            for mask in itertools.product([False,True],repeat=3):
                item=deepcopy(base)
                for i,hidden in enumerate(mask):
                    if hidden:item['grammar']['frames'][i][field]=False if field=='slots_complete' else 'unknown'
                scores[mask]=self.rank(item)['grammar_score']
            for left,right in itertools.product(scores,repeat=2):
                if all(not a or b for a,b in zip(left,right)):
                    self.assertLessEqual(scores[right],scores[left],(field,left,right))
    def test_unknown_link_duplicate_is_one_missing_observation(self):
        text='如果甲，乙。由于丙，丁。'
        def bound(part):
            start=text.index(part);return dict(start=start,end=start+len(part),text=part)
        links=[dict(kind='link',relation=relation,**bound(full),slots=[dict(role=role,**bound(part)) for role,part in [('dependent',dep),('head',head)]])
               for relation,full,dep,head in [('condition','如果甲，乙','如果甲','乙'),('cause','由于丙，丁','由于丙','丁')]]
        item=dict(id='one',label='Keep',text=text,grammar=dict(version=6,source='writing-model',text_sha256=hashlib.sha256(text.encode()).hexdigest(),frames=links))
        self.choose(item);item['grammar']['frames'][0]['relation']='unknown'
        before=self.rank(item);add_duplicate(item);after=self.rank(item)
        self.assertEqual(before['score'],after['score'])
        self.assertEqual(learn._grammar_unknown_counts(after['grammar'])['link'],1)
    def test_unresolved_adjunct_duplicate_does_not_dilute_other_placement(self):
        data=json.loads((ROOT/'tests/fixtures/link-order.json').read_text())['writers']['a']['training']
        item=deepcopy(data['candidates'][0]);item['id']='one';self.choose(item);before=self.rank(item)
        index=next(i for i,f in enumerate(item['grammar']['frames']) if f['kind']=='link' and f['relation']=='condition')
        add_duplicate(item,index);after=self.rank(item)
        self.assertEqual(before['grammar_scores_by_family']['adjunct_position'],after['grammar_scores_by_family']['adjunct_position'])
        self.assertEqual(before['score'],after['score'])
    def test_conflicting_expression_scopes_are_not_replaced_by_a_representative(self):
        pools=json.loads((ROOT/'tests/fixtures/grammar-adjunct-placement.json').read_text())['candidates']
        item=deepcopy(pools[1]);matrix=deepcopy(item['grammar']['frames'][0]);matrix.update(start=11,end=15,text='保持稳定')
        matrix['slots']=[s for s in matrix['slots'] if s['role']!='patient'];item['grammar']['frames'].append(matrix)
        validated=learn._validated_candidates([item])[0]
        self.assertIsNone(learn._grammar_adjunct_positions(validated['grammar'],require_complete=True))
    def test_identical_scores_do_not_make_changed_payloads_replayable(self):
        original=candidate();receipt=self.choose(original);snapshot=self.dump()
        modified=deepcopy(original);add_duplicate(modified)
        with self.assertRaises(ValueError):self.choose(modified)
        self.assertEqual(self.dump(),snapshot)
        self.assertEqual(self.choose(original),{**receipt,'replayed':True})


if __name__=='__main__':unittest.main()
