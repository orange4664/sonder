"""Recipient-aware argument order, including old-protocol feedback boundaries."""
from contextlib import closing
from copy import deepcopy
import importlib.util,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('recipient_tests',ROOT/'library/learn.py')
learn=importlib.util.module_from_spec(spec);spec.loader.exec_module(learn)
PREFIX=learn.GRAMMAR_PREFIX

def draft(order='recipient_first',*,subject='We',event='told',patient='the result',recipient='the operator',version=None):
    text=(f'{subject} {event} {recipient} {patient}.' if order=='recipient_first'
          else f'{subject} {event} {patient} to {recipient}.')
    grammar={'frames':[{'kind':'expression','text':text,'form':'verbal','voice':'active',
              'slots':[{'role':k,'text':v} for k,v in [('agent',subject),('event',event),('patient',patient),('recipient',recipient)]]}]}
    if version is not None:grammar['version']=version
    return {'round_id':'draft-'+order,'learning_context':{'section':'methods','role':'detail'},
            'candidates':[{'id':order,'label':'Neutral','text':text,'analysis':{'units':[]},'grammar':grammar}]}

def legacy(candidate):
    result=deepcopy(candidate);result['grammar']['version']=4
    for f in result['grammar']['frames']:f['slots']=[s for s in f['slots'] if s['role']!='recipient']
    return result

def shape(feature):
    return feature.startswith((PREFIX+'expression_slots:',PREFIX+'expression:'))

class RecipientTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='sonder-recipient-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.db=str(self.root/'preference.db');self.file=self.root/'choice.json'
        clock=patch.object(learn,'_now_iso',return_value='2026-10-27T05:00:00+00:00');clock.start();self.addCleanup(clock.stop)

    def bind(self,order='recipient_first',**kwargs):
        try:return learn.bind_candidate_data(draft(order,**kwargs))['candidates'][0]
        except ValueError as error:self.fail('recipient draft must bind: '+str(error))

    def choose(self,pool,chosen,rid,**extra):
        self.file.write_text(json.dumps({'round_id':rid,'learning_context':{'section':'methods','role':'detail'},'candidates':pool,'chosen':chosen,'author_owned':True,**extra}))
        return learn.choice_file(self.db,'methods','detail',self.file)

    def shape_rows(self):
        with closing(sqlite3.connect(self.db)) as c:
            return [r for r in c.execute('SELECT * FROM syntax_prefs ORDER BY feature').fetchall() if shape(r[1])]

    def grammar_only(self):
        with closing(sqlite3.connect(self.db)) as c,c:
            c.execute('DELETE FROM prefs');c.execute('DELETE FROM phrase_prefs')
            c.execute('DELETE FROM syntax_prefs WHERE feature NOT LIKE ?',(PREFIX+'%',))

    def test_version5_binding_exact_order_and_legacy_defaults(self):
        item=self.bind();self.assertEqual(item['grammar']['version'],5)
        self.assertIn(5,learn.engine_info()['input_versions']['grammar'])
        roles=[s['role'] for s in item['grammar']['frames'][0]['slots']]
        self.assertEqual(roles,['agent','event','recipient','patient'])
        self.assertIn(PREFIX+'expression_slots:verbal:agent>event>recipient>patient',learn._grammar_features(item['grammar']))
        for version in (1,2,3,4):
            with self.subTest(version=version),self.assertRaises(ValueError):learn.bind_candidate_data(draft(version=version))
        old=draft();old['candidates'][0]['grammar']['frames'][0]['slots'].pop()
        expected=learn.bind_candidate_data(old);self.assertEqual(expected['candidates'][0]['grammar']['version'],2)
        raw=self.root/'old.json';out=self.root/'bound.json';raw.write_text(json.dumps(old))
        out.write_text(json.dumps(expected,ensure_ascii=False,sort_keys=True,indent=2)+'\n');before=out.read_bytes(),out.stat().st_mtime_ns
        self.assertTrue(learn.bind_file(raw,out)['reused']);self.assertEqual((out.read_bytes(),out.stat().st_mtime_ns),before)
        self.assertFalse(Path(self.db).exists())

    def test_invalid_recipient_evidence_duplicates_and_link_role_fail_before_store(self):
        item=self.bind();bad=[]
        for kind in ('overlap','duplicate','boolean','stale','link'):
            c=deepcopy(item);frame=c['grammar']['frames'][0];recipient=next(s for s in frame['slots'] if s['role']=='recipient')
            if kind=='overlap':recipient.update({k:next(s for s in frame['slots'] if s['role']=='patient')[k] for k in ('start','end','text')})
            elif kind=='duplicate':frame['slots'].append(deepcopy(recipient))
            elif kind=='boolean':recipient['start']=True
            elif kind=='stale':recipient['text']='invented person'
            else:frame.update(kind='link',relation='complement');frame.pop('form');frame.pop('voice')
            bad.append(c)
        for c in bad:
            self.file.write_text(json.dumps({'round_id':'bad','chosen':c['id'],'candidates':[c]}))
            with self.assertRaises(ValueError):learn.choice_file(self.db,'methods','detail',self.file)
            self.assertFalse(Path(self.db).exists())

    def test_recipient_order_transfers_to_unseen_words_and_corrects_softly(self):
        pool=[self.bind(o) for o in ('recipient_first','patient_first')]
        held=[self.bind(o,subject='The team',event='gave',patient='new settings',recipient='the technician') for o in ('patient_first','recipient_first')]
        for i in range(2):self.choose(pool,'recipient_first','first-'+str(i))
        self.grammar_only();r=learn.rank_candidates(self.db,'methods','detail',held)
        self.assertEqual(r[0]['id'],'recipient_first');self.assertGreater(r[0]['grammar_score'],r[1]['grammar_score'])
        self.assertTrue(all(x['phrase_score']==x['label_weight']==x['base_syntax_score']==x['analysis_topology_score']==0 for x in r))
        self.assertTrue(all(x['score']==0 for x in learn.rank_candidates(self.db,'results','detail',held)))
        with patch.object(learn,'_now_iso',return_value='2026-12-26T05:00:00+00:00'):
            decayed=learn.rank_candidates(self.db,'methods','detail',held)
        self.assertGreater(decayed[0]['score'],0);self.assertLess(decayed[0]['score'],r[0]['score'])
        for i in range(4):self.choose(held,'patient_first','reverse-'+str(i))
        self.grammar_only();after=learn.rank_candidates(self.db,'methods','detail',held[::-1])
        self.assertEqual(after[0]['id'],'patient_first');self.assertGreater(after[0]['grammar_score'],after[1]['grammar_score'])

    def test_old_and_new_inputs_do_not_change_old_scores_without_recipient(self):
        old=[legacy(self.bind(o)) for o in ('recipient_first','patient_first')]
        self.choose(old,'recipient_first','legacy')
        modern=deepcopy(old)
        for c in modern:c['grammar']['version']=5
        a=learn.rank_candidates(self.db,'methods','detail',old);b=learn.rank_candidates(self.db,'methods','detail',modern)
        self.assertEqual([(x['id'],x['score']) for x in a],[(x['id'],x['score']) for x in b])
        self.assertEqual(learn._grammar_features(old[0]['grammar']),learn._grammar_features(modern[0]['grammar']))

    def test_mixed_versions_do_not_reject_unrepresented_argument_shapes_either_way(self):
        old=legacy(self.bind('recipient_first'));modern=self.bind('patient_first')
        # Seed both representations so a mistaken penalty has observable state.
        self.choose([old],old['id'],'old-seed');self.choose([modern],modern['id'],'new-seed')
        before={r[1]:r for r in self.shape_rows()}
        result=self.choose([old,modern],old['id'],'choose-old')
        self.assertTrue(result['grammar_negative_feedback']);self.assertFalse(result.get('argument_shape_negative_feedback',True))
        after={r[1]:r for r in self.shape_rows()}
        modern_keys={f for f in learn._grammar_features(modern['grammar']) if shape(f)}
        self.assertTrue(all(after[k]==before[k] for k in modern_keys))
        before=after;result=self.choose([old,modern],modern['id'],'choose-new')
        self.assertFalse(result.get('argument_shape_negative_feedback',True))
        after={r[1]:r for r in self.shape_rows()}
        old_keys={f for f in learn._grammar_features(old['grammar']) if shape(f)}
        self.assertTrue(all(after[k]==before[k] for k in old_keys))

    def test_complete_v5_can_reject_shapes_and_unknown_off_optout_replay_stay_safe(self):
        pool=[self.bind(o) for o in ('recipient_first','patient_first')]
        first=self.choose(pool,'recipient_first','first');self.assertTrue(first.get('argument_shape_negative_feedback',False))
        before=self.shape_rows();self.assertEqual(self.choose(pool,'recipient_first','first'),{**first,'replayed':True});self.assertEqual(self.shape_rows(),before)
        self.assertFalse(self.choose(pool,'patient_first','skip',learn_from_choice=False)['feedback_applied']);self.assertEqual(self.shape_rows(),before)
        masked=deepcopy(pool)
        masked[0]['grammar']['frames'][0]['form']='unknown'
        r=learn.rank_candidates(self.db,'methods','detail',[pool[0]])[0];m=learn.rank_candidates(self.db,'methods','detail',[masked[0]])[0]
        self.assertLessEqual(m['score'],r['score'])
        partial=self.choose(masked,'patient_first','partial');self.assertFalse(partial['grammar_negative_feedback']);self.assertFalse(partial.get('argument_shape_negative_feedback',True))
        self.choose(pool,'patient_first','new-complete')
        after=self.shape_rows();self.assertNotEqual(after,before)
        learn.set_syntax_backend(self.db,'off')
        self.assertTrue(all(row['grammar_score']==row['syntax_score']==0 for row in learn.rank_candidates(self.db,'methods','detail',pool)))


if __name__=='__main__':unittest.main()
