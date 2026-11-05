"""Learn temporal clause placement from grounded evidence, distinct from conditions."""
from contextlib import closing
from copy import deepcopy
import importlib.util,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('temporal',ROOT/'library/learn.py')
learn=importlib.util.module_from_spec(spec);spec.loader.exec_module(learn)
PREFIX=learn.GRAMMAR_PREFIX+'adjunct_position:'


def draft(position, *, agent='探头', event='记录', patient='电势', subject='炉温', state='稳定', relation='temporal', version=3):
    dependent=f'在{subject}{state}后'
    main=agent+event+patient
    text={'before':dependent+'，'+main+'。','embedded':agent+dependent+event+patient+'。','after':main+'，'+dependent+'。'}[position]
    matrix_text=text if position=='embedded' else main
    grammar={'source':'writing-model','frames':[
        {'kind':'expression','text':matrix_text,'form':'verbal','voice':'active',
         'slots':[{'role':role,'text':value} for role,value in [('agent',agent),('event',event),('patient',patient)]]},
        {'kind':'expression','text':subject+state,'form':'adjectival','voice':'nonagentive',
         'slots':[{'role':'patient','text':subject},{'role':'event','text':state}]},
        {'kind':'link','text':text,'relation':relation,'slots':[{'role':'head','text':event+patient},{'role':'dependent','text':dependent}]}]}
    if version is not None:grammar['version']=version
    return {'round_id':'draft-'+position,'candidates':[{'id':position,'label':'neutral','text':text,'analysis':{'spans':[]},'grammar':grammar}]}


class TemporalClauseTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='sonder-temporal-');self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.db=str(self.root/'preference.db');self.file=self.root/'decision.json'
        clock=patch.object(learn,'_now_iso',return_value='2026-10-27T01:00:00+00:00');clock.start();self.addCleanup(clock.stop)

    def bind(self,position,**kwargs):
        try:return learn.bind_candidate_data(draft(position,**kwargs))['candidates'][0]
        except ValueError as error:self.fail('temporal clause binding must be supported: '+str(error))

    def choose(self,pool,chosen,event):
        self.file.write_text(json.dumps({'round_id':event,'candidates':pool,'chosen':chosen,'author_owned':True},ensure_ascii=False))
        return learn.choice_file(self.db,'methods','support',self.file)

    def position_rows(self):
        with closing(sqlite3.connect(self.db)) as c:
            return c.execute('SELECT * FROM syntax_prefs WHERE feature LIKE ? ORDER BY feature',(PREFIX+'%',)).fetchall()

    def test_new_drafts_use_v3_and_older_versions_do_not_silently_accept_temporal(self):
        candidate=self.bind('before',version=None)
        self.assertEqual(candidate['grammar']['version'],3)
        self.assertIn(3,learn.engine_info()['input_versions']['grammar'])
        for version in (1,2):
            old = draft('before',version=version)
            if version == 1:
                old['candidates'][0]['grammar']['frames'][1]['form'] = 'unknown'
            with self.subTest(version=version),self.assertRaisesRegex(ValueError,'relation'):
                learn.bind_candidate_data(old)
        self.assertFalse(Path(self.db).exists())
        # An explicit existing version keeps the old validation contract.
        old=draft('before',relation='condition',version=2)
        self.assertEqual(learn.bind_candidate_data(old)['candidates'][0]['grammar']['version'],2)

    def test_ordinary_omitted_version_retries_keep_the_prior_v2_bytes(self):
        ordinary=draft('before',relation='condition',version=None)
        explicit=deepcopy(ordinary);explicit['candidates'][0]['grammar']['version']=2
        old_bound=learn.bind_candidate_data(explicit)
        draft_file=self.root/'old-draft.json';output=self.root/'old-bound.json'
        draft_file.write_text(json.dumps(ordinary,ensure_ascii=False))
        output.write_text(json.dumps(old_bound,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
        before=output.read_bytes()
        try:receipt=learn.bind_file(draft_file,output)
        except ValueError as error:self.fail('same historical draft must be a safe retry: '+str(error))
        self.assertTrue(receipt['reused']);self.assertEqual(output.read_bytes(),before)
        self.assertFalse(Path(self.db).exists())

    def test_front_embedded_and_postposed_temporal_positions_have_grounded_keys(self):
        for position in ('before','embedded','after'):
            candidate=self.bind(position)
            keys={f for f in learn._grammar_features(candidate['grammar']) if f.startswith(PREFIX)}
            self.assertEqual(keys,{PREFIX+'temporal:'+position})
            self.assertIn(learn.GRAMMAR_PREFIX+'link:temporal',learn._grammar_features(candidate['grammar']))
            self.assertFalse(any('condition' in f for f in learn._grammar_features(candidate['grammar'])))

    def test_author_feedback_transfers_corrects_and_stays_in_context(self):
        training=[self.bind(position) for position in ('before','embedded')]
        held=[self.bind(position,agent='仪器',event='测量',patient='相位',subject='腔压',state='恒定') for position in ('embedded','before')]
        for i in range(3):self.choose(training,'before','front-'+str(i))
        rows=learn.rank_candidates(self.db,'methods','support',held)
        self.assertEqual(rows[0]['id'],'before')
        self.assertGreater(rows[0]['grammar_scores_by_family']['adjunct_position'],rows[1]['grammar_scores_by_family']['adjunct_position'])
        for i in range(5):self.choose(held,'embedded','embed-'+str(i))
        corrected=learn.rank_candidates(self.db,'methods','support',held[::-1])
        self.assertEqual(corrected[0]['id'],'embedded')
        other=learn.rank_candidates(self.db,'results','support',held)
        self.assertTrue(all(row['grammar_score']==0 for row in other))

    def test_condition_position_memory_is_not_temporal_position_memory(self):
        # Only the interpretation differs here; this is a source-isolation probe,
        # not a semantic claim that a temporal sentence is a logical condition.
        pool=[self.bind(position,relation='condition',version=2) for position in ('before','embedded')]
        self.choose(pool,'before','condition-control')
        held=[self.bind(position) for position in ('before','embedded')]
        rows=learn.rank_candidates(self.db,'methods','support',held)
        self.assertTrue(all(row['grammar_scores_by_family']['adjunct_position']==0 for row in rows))

    def test_unresolved_temporal_placement_does_not_reject_known_position(self):
        pool=[self.bind(position) for position in ('before','embedded')]
        self.choose(pool,'before','initial')
        before=self.position_rows()
        changed=deepcopy(pool)
        matrix=next(f for f in changed[1]['grammar']['frames'] if f['kind']=='expression' and f['voice']=='active')
        matrix['slots']=[s for s in matrix['slots'] if s['role']!='agent']
        receipt=self.choose(changed,'embedded','unresolved')
        self.assertFalse(receipt['adjunct_position_negative_feedback'])
        self.assertEqual(self.position_rows(),before)
        unknown=deepcopy(pool)
        for frame in unknown[1]['grammar']['frames']:
            if frame['kind']=='link':frame['relation']='unknown'
        receipt=self.choose(unknown,'embedded','unknown-relation')
        self.assertFalse(receipt['grammar_negative_feedback'])
        self.assertEqual(self.position_rows(),before)


if __name__=='__main__':unittest.main()
