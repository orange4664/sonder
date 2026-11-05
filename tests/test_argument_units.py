"""Disjoint multi-part evidence preserves unit identity and learns surface organization."""
from contextlib import closing
from copy import deepcopy
import hashlib, importlib.util, json, sqlite3, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('argument_units', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec); spec.loader.exec_module(learn)
TOPOLOGY = 'syntax:analysis-topology-v1:writing-model:'


def draft(position='embedded', *, subject='研究者', method='通过逐点扫描', predicate='测得平均响应', version=None):
    matrix = subject + predicate
    text = {'before': method + '，' + matrix + '。', 'embedded': subject + method + predicate + '。',
            'after': matrix + '，' + method + '。'}[position]
    parts = [subject, predicate] if position == 'embedded' else [matrix]
    units = [{'move': 'result', 'polarity': 'affirmative', 'certainty': 'asserted',
              'parts': [{'text': p} for p in parts]},
             {'move': 'method', 'polarity': 'affirmative', 'certainty': 'asserted', 'parts': [{'text': method}]}]
    analysis = {'units': units}
    if version is not None: analysis['version'] = version
    return {'round_id': 'draft-' + position, 'learning_context': {'section':'results','role':'support'},
            'candidates': [{'id':position, 'label':'Neutral', 'text':text,
                            'analysis':analysis, 'grammar':{'frames':[]}}]}


def as_v1(candidate):
    old = deepcopy(candidate)
    spans = []
    for unit in old['analysis'].pop('units'):
        assert len(unit['parts']) == 1
        spans.append({**unit['parts'][0], **{k:unit[k] for k in ('move','polarity','certainty')}})
    old['analysis'].update(version=1, spans=sorted(spans,key=lambda p:p['start']))
    return old


class ArgumentUnitTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='sonder-units-');self.addCleanup(folder.cleanup)
        self.root=Path(folder.name);self.db=str(self.root/'preference.db');self.file=self.root/'choice.json'
        clock=patch.object(learn,'_now_iso',return_value='2026-10-27T01:00:00+00:00');clock.start();self.addCleanup(clock.stop)

    def bind(self,position='embedded',**kwargs):
        try:return learn.bind_candidate_data(draft(position,**kwargs))['candidates'][0]
        except ValueError as error:self.fail('unit draft must bind: '+str(error))

    def choose(self,pool,chosen,round_id):
        self.file.write_text(json.dumps({'round_id':round_id,'candidates':pool,'chosen':chosen,'author_owned':True},ensure_ascii=False))
        return learn.choice_file(self.db,'results','support',self.file)

    def topology_rows(self):
        with closing(sqlite3.connect(self.db)) as c:
            return c.execute('SELECT * FROM syntax_prefs WHERE feature LIKE ? ORDER BY feature',(TOPOLOGY+'%',)).fetchall()

    def test_v2_binding_is_exact_and_canonical_while_v1_retries_stay_unchanged(self):
        raw=draft();bound=self.bind()
        self.assertEqual(bound['analysis']['version'],2)
        self.assertIn(2,learn.engine_info()['input_versions']['analysis'])
        self.assertEqual(learn.engine_info()['feature_versions'].get('analysis_topology'),'v1')
        permuted=deepcopy(raw);permuted['candidates'][0]['analysis']['units'].reverse()
        for u in permuted['candidates'][0]['analysis']['units']:u['parts'].reverse()
        self.assertEqual(learn.bind_candidate_data(permuted)['candidates'][0],bound)
        old=deepcopy(raw);old['candidates'][0]['analysis']={'spans':[]}
        old_file=self.root/'old.json';target=self.root/'bound.json';old_file.write_text(json.dumps(old,ensure_ascii=False))
        expected=learn.bind_candidate_data(old)
        self.assertEqual(expected['candidates'][0]['analysis']['version'],1)
        target.write_text(json.dumps(expected,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
        before=target.read_bytes(),target.stat().st_mtime_ns
        self.assertTrue(learn.bind_file(old_file,target)['reused'])
        self.assertEqual((target.read_bytes(),target.stat().st_mtime_ns),before)
        with self.assertRaisesRegex(ValueError,'analysis'):learn.bind_candidate_data(draft(version=1))
        self.assertFalse(Path(self.db).exists())

    def test_invalid_parts_overlap_gaps_units_and_versions_fail_before_store(self):
        base=self.bind()
        changes=[]
        def changed(fn):
            item=deepcopy(base);fn(item['analysis']);changes.append(item)
        changed(lambda a:a['units'][0].update(parts=[]))
        changed(lambda a:a['units'].append(deepcopy(a['units'][0])))
        changed(lambda a:a['units'][0]['parts'].pop())
        changed(lambda a:a['units'][0]['parts'][0].update(start=True))
        changed(lambda a:a['units'][0]['parts'][0].update(text='改写过的文字'))
        changed(lambda a:a['units'][0].update(move='invented-role'))
        changed(lambda a:a['units'][0].update(id='not-a-protocol-field'))
        changed(lambda a:a.update(version=3))
        changed(lambda a:a.update(spans=[]))
        changed(lambda a:a.update(units=a['units']*9))
        for item in changes:
            with self.subTest(analysis=item['analysis']):
                self.file.write_text(json.dumps({'round_id':'invalid','candidates':[item],'chosen':item['id']}))
                with self.assertRaises(ValueError):learn.choice_file(self.db,'results','support',self.file)
                self.assertFalse(Path(self.db).exists())

    def test_repeated_fragment_requires_selector_and_preserves_unicode(self):
        text='样本变化，样本变化。'
        raw={'round_id':'repeated','candidates':[{'id':'a','text':text,'analysis':{'units':[
            {'move':'result','polarity':'affirmative','certainty':'asserted','parts':[{'text':'样本变化'}]},
            {'move':'result','polarity':'affirmative','certainty':'asserted','parts':[{'text':'样本变化','occurrence':2}]}]},'grammar':{'frames':[]}}]}
        with self.assertRaisesRegex(ValueError,'ambiguous|multiple|occurrence'):learn.bind_candidate_data(raw)
        raw['candidates'][0]['analysis']['units'][0]['parts'][0]['occurrence']=1
        try:bound=learn.bind_candidate_data(raw)['candidates'][0]
        except ValueError as error:self.fail('explicit repeated evidence must bind: '+str(error))
        self.assertEqual(bound['text'],text)
        self.assertEqual([u['parts'][0]['start'] for u in bound['analysis']['units']],[0,5])

    def test_unit_identity_distinguishes_interruption_and_ignores_harmless_part_splits(self):
        item=self.bind();features=set(learn._analysis_features(item['analysis']))
        self.assertIn(TOPOLOGY+'pair:result>method:ABA',features)
        raw=draft();raw['candidates'][0]['analysis']['units'][0]['parts']=[{'text':'研究'},{'text':'者'},{'text':'测得平均响应'}]
        split=learn.bind_candidate_data(raw)['candidates'][0]
        self.assertEqual(features,set(learn._analysis_features(split['analysis'])))
        self.assertEqual(learn._analysis_feature_capacity(item['analysis']),learn._analysis_feature_capacity(split['analysis']))
        # Identity control only: not a proposed linguistic reading of the subject fragment.
        independent=deepcopy(item)
        unit=independent['analysis']['units'].pop(0)
        independent['analysis']['units'] += [{**unit,'parts':[part]} for part in unit['parts']]
        independent=learn._validated_candidates([independent])[0]
        self.assertNotIn(TOPOLOGY+'pair:result>method:ABA',learn._analysis_features(independent['analysis']))

    def test_untrained_topology_does_not_dilute_existing_contiguous_memory(self):
        front=self.bind('before');after=self.bind('after')
        old=as_v1(front);old_after=as_v1(after)
        self.choose([old,old_after],'before','legacy-learning')
        old_score=learn.rank_candidates(self.db,'results','support',[old])[0]
        new_score=learn.rank_candidates(self.db,'results','support',[front])[0]
        self.assertEqual(old_score['score'],new_score['score'])
        self.assertEqual(old_score['base_syntax_score'],new_score['base_syntax_score'])
        self.assertEqual(new_score.get('analysis_topology_score'),0)
        self.assertEqual(self.topology_rows(),[])

    def test_topology_alone_transfers_corrects_and_stays_context_local(self):
        pool=[self.bind('before'),self.bind('embedded')]
        held=[self.bind(p,subject='团队',method='利用独立标定',predicate='获得噪声估计') for p in ('before','embedded')]
        for i in range(2):self.choose(pool,'embedded','embedded-'+str(i))
        with closing(sqlite3.connect(self.db)) as c,c:
            c.execute('DELETE FROM prefs');c.execute('DELETE FROM phrase_prefs')
            c.execute('DELETE FROM syntax_prefs WHERE feature NOT LIKE ?',(TOPOLOGY+'%',))
        rows=learn.rank_candidates(self.db,'results','support',held)
        self.assertEqual(rows[0]['id'],'embedded');self.assertGreater(rows[0]['score'],rows[1]['score'])
        self.assertTrue(all(r['base_syntax_score']==r['grammar_score']==r['phrase_score']==r['label_weight']==0 for r in rows))
        self.assertTrue(all(r['score']==0 for r in learn.rank_candidates(self.db,'methods','support',held)))
        for i in range(4):self.choose(held,'before','before-'+str(i))
        after=learn.rank_candidates(self.db,'results','support',held[::-1])
        self.assertEqual(after[0]['id'],'before')

    def test_mixed_v1_choices_cannot_reject_missing_topology(self):
        front=self.bind('before');embedded=self.bind('embedded')
        first=self.choose([front,embedded],'embedded','first')
        self.assertTrue(first.get('analysis_topology_negative_feedback',False))
        before=self.topology_rows()
        receipt=self.choose([as_v1(front),embedded],'before','legacy-choice')
        self.assertTrue(receipt['analysis_negative_feedback'])
        self.assertFalse(receipt.get('analysis_topology_negative_feedback',True))
        self.assertEqual(self.topology_rows(),before)
        self.choose([front,embedded],'before','new-choice')
        self.assertNotEqual(self.topology_rows(),before)

    def test_unknown_masking_off_optout_and_replay_preserve_their_contracts(self):
        item=self.bind()
        def weight(conn,context,feature,now=None):return int(hashlib.sha256(feature.encode()).hexdigest()[:8],16)%3001,1
        with patch.object(learn,'_syntax_weight_of',side_effect=weight):
            initial=learn.rank_candidates(self.db,'results','support',[item])[0]
            for i in range(len(item['analysis']['units'])):
                for field in ('move','polarity','certainty'):
                    masked=deepcopy(item);masked['analysis']['units'][i][field]='unknown'
                    row=learn.rank_candidates(self.db,'results','support',[masked])[0]
                    self.assertLessEqual(row['score'],initial['score'])
                    self.assertLessEqual(set(learn._analysis_features(masked['analysis'])),set(learn._analysis_features(item['analysis'])))
        receipt=self.choose([item],'embedded','learned')
        before=self.topology_rows();self.assertEqual(self.choose([item],'embedded','learned'),{**receipt,'replayed':True});self.assertEqual(self.topology_rows(),before)
        saved=json.loads(self.file.read_text());saved.update(round_id='excluded',learn_from_choice=False);self.file.write_text(json.dumps(saved))
        self.assertFalse(learn.choice_file(self.db,'results','support',self.file)['feedback_applied']);self.assertEqual(self.topology_rows(),before)
        learn.set_syntax_backend(self.db,'off')
        row=learn.rank_candidates(self.db,'results','support',[item])[0]
        self.assertEqual(row.get('analysis_topology_score'),0);self.assertEqual(row['syntax_score'],0)

    def test_masking_weak_signature_does_not_amplify_learned_pair_preference(self):
        asserted = self.bind()
        tentative = draft(predicate='可能测得平均响应')
        tentative['candidates'][0]['analysis']['units'][0]['certainty'] = 'hedged'
        hedged = learn.bind_candidate_data(tentative)['candidates'][0]
        self.choose([asserted], 'embedded', 'asserted-event')
        self.choose([hedged], 'embedded', 'hedged-event')
        # Both decisions reinforce the same pair trace, but distinct strength
        # signatures each receive only one reward. Masking the weaker signature
        # must not amplify the shared trace's contribution.
        known = learn.rank_candidates(self.db,'results','support',[asserted])[0]
        hidden = deepcopy(asserted)
        hidden['analysis']['units'][0]['certainty'] = 'unknown'
        masked = learn.rank_candidates(self.db,'results','support',[hidden])[0]
        self.assertEqual(known['analysis_topology_capacity'], 2)
        self.assertEqual(masked['analysis_topology_capacity'], 2)
        self.assertLess(masked['analysis_topology_score'], known['analysis_topology_score'])
        self.assertLessEqual(masked['score'], known['score'])


if __name__=='__main__':unittest.main()
