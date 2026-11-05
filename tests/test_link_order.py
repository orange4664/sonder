"""Independent grammatical relations must retain their observed surface order."""
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

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('link_order_learn', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(learn)
PREFIX = 'syntax:grammar-v1:writing-model:link_pair_order:'
NOW = '2026-10-27T12:00:00+00:00'


def links_candidate(parts, *, identifier='synthetic'):
    """Hand-specified independent or nested relation evidence, no cue inference."""
    text = '甲后，乙。如果丙，丁。由于戊，己。'
    def span(fragment):
        start = text.index(fragment)
        return dict(start=start, end=start+len(fragment), text=fragment)
    frames = []
    for relation, dependent, head, scope in parts:
        frames.append(dict(kind='link', relation=relation, **span(scope), slots=[
            dict(role='dependent', **span(dependent)), dict(role='head', **span(head))]))
    return dict(id=identifier, label='Equal', text=text, grammar=dict(version=6,
                source='writing-model', text_sha256=hashlib.sha256(text.encode()).hexdigest(), frames=frames))


def replace_atom(candidate, old, new):
    """Different synthetic wording; do not bypass identical-text validation."""
    assert len(old)==len(new)
    candidate['text']=candidate['text'].replace(old,new)
    candidate['grammar']['text_sha256']=hashlib.sha256(candidate['text'].encode()).hexdigest()
    for frame in candidate['grammar']['frames']:
        frame['text']=frame['text'].replace(old,new)
        for slot in frame['slots']:slot['text']=slot['text'].replace(old,new)


THREE = [('temporal','甲后','乙','甲后，乙。'),
         ('condition','如果丙','丁','如果丙，丁。'),
         ('cause','由于戊','己','由于戊，己。')]


class LinkOrderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sonder-link-order-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = str(self.root/'preference.db')
        self.fixture = json.loads((ROOT/'tests/fixtures/link-order.json').read_text())['writers']
        patcher = patch.object(learn, '_now_iso', return_value=NOW)
        patcher.start(); self.addCleanup(patcher.stop)

    def choose(self, data, selected='forward', event='first'):
        data = deepcopy(data)
        data.update(chosen=selected, author_owned=selected=='forward', round_id=event)
        path = self.root/'choice.json'; path.write_text(json.dumps(data,ensure_ascii=False))
        return learn.choice_file(self.db, 'methods', 'detail', path)

    def rank(self, candidates, section='methods'):
        return {r['id']:r for r in learn.rank_candidates(self.db, section, 'detail', candidates)}

    def pair_features(self, c):
        candidate = learn._validated_candidates([c])[0]
        return {f for f in learn._grammar_features(candidate['grammar']) if f.startswith(PREFIX)}

    def snapshot(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return list(conn.iterdump())

    def test_actual_same_move_new_words_transfer_across_both_writers(self):
        for writer in 'ab':
            self.choose(self.fixture[writer]['training'], event=writer)
            for query in 'ab':
                pool = self.fixture[query]['query']['candidates']
                result = self.rank(pool[::-1])
                self.assertGreater(result['forward']['score'], result['reverse']['score'])
                self.assertGreater(result['forward']['grammar_scores_by_family']['link_pair_order'], 0)
                self.assertEqual(result['forward']['analysis_score'], result['reverse']['analysis_score'])
                self.assertEqual(result['forward']['analysis_topology_score'], result['reverse']['analysis_topology_score'])

    def test_old_weights_scores_and_receipt_remain_exact_without_new_feedback(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.executescript((ROOT/'tests/fixtures/link-order-legacy.sql').read_text())
            receipt = json.loads(conn.execute('SELECT result_json FROM choice_events').fetchone()[0])
        def history():
            with closing(sqlite3.connect(self.db)) as conn:
                rows = {table: conn.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()
                        for table in ('prefs', 'phrase_prefs', 'syntax_prefs', 'seen', 'choice_events')}
                rows['authors'] = conn.execute(
                    'SELECT id,kind,text,created,last_at,rewards FROM exemplars ORDER BY id').fetchall()
                return rows
        original_history = history()
        # The v4 fixture permits a derived-index migration, but no author or
        # feedback history change. Freeze the complete dump after that upgrade.
        with closing(learn._open(self.db)):
            pass
        self.assertEqual(history(), original_history)
        snapshot = self.snapshot()
        result = self.rank(self.fixture['a']['query']['candidates'])
        self.assertEqual(result['forward']['score'], result['reverse']['score'])
        self.assertTrue(all(r['grammar_scores_by_family']['link_pair_order']==0 for r in result.values()))
        replay = self.choose(self.fixture['a']['training'], event=self.fixture['a']['training']['round_id'])
        self.assertEqual(replay, {**receipt, 'replayed':True})
        self.assertEqual(self.snapshot(), snapshot)

    def test_order_preference_is_local_decays_and_can_be_corrected(self):
        self.choose(self.fixture['a']['training'])
        query = self.fixture['a']['query']; pool = query['candidates']
        first = self.rank(pool)
        self.assertGreater(first['forward']['score'],first['reverse']['score'])
        self.assertTrue(all(r['score']==0 for r in self.rank(pool,'discussion').values()))
        with patch.object(learn,'_now_iso',return_value='2026-12-26T12:00:00+00:00'):
            faded = self.rank(pool)
        self.assertLess(faded['forward']['grammar_scores_by_family']['link_pair_order'],
                        first['forward']['grammar_scores_by_family']['link_pair_order'])
        for i in range(6): self.choose(query,'reverse',f'reverse-{i}')
        changed = self.rank(pool)
        self.assertGreater(changed['reverse']['score'],changed['forward']['score'])

    def test_frame_and_slot_presentation_order_and_padding_do_not_change_pairs(self):
        original = self.fixture['b']['training']['candidates'][0]
        expected = self.pair_features(original)
        self.assertTrue(expected)
        modified = deepcopy(original)
        modified['grammar']['frames'].reverse()
        for f in modified['grammar']['frames']:
            f['slots'].reverse()
            if f['kind']=='link':
                end=f['end']; text=modified['text']
                if end<len(text) and text[end] in '。；':
                    f.update(end=end+1,text=text[f['start']:end+1])
        self.assertEqual(self.pair_features(modified),expected)

    def test_pair_keys_use_relations_and_slot_order_not_rhetorical_labels(self):
        c = links_candidate(THREE)
        features = self.pair_features(c)
        self.assertEqual(len(features),3)
        self.assertIn(PREFIX+'temporal/dependent>head||condition/dependent>head',features)
        changed=deepcopy(c)
        for s in changed['grammar']['frames'][0]['slots']:
            s['role']='head' if s['role']=='dependent' else 'dependent'
        self.assertNotEqual(self.pair_features(changed),features)

    def test_masking_one_or_more_relations_cannot_raise_pair_score(self):
        c=links_candidate(THREE); features=self.pair_features(c)
        self.assertEqual(len(features),3)
        with closing(learn._open(self.db)) as conn,conn:
            for f in features:
                value=9000 if 'temporal/dependent>head||condition/' in f else 1
                conn.execute('INSERT INTO syntax_prefs VALUES (?,?,?,?,?)',('methods::detail',f,value,1,NOW))
        scores={}
        for mask in itertools.product([False,True],repeat=3):
            candidate=deepcopy(c)
            for i,hidden in enumerate(mask):
                if hidden:candidate['grammar']['frames'][i]['relation']='unknown'
            scores[mask]=self.rank([candidate])[c['id']]['grammar_scores_by_family']['link_pair_order']
        for left,right in itertools.product(scores,repeat=2):
            if all(not a or b for a,b in zip(left,right)):
                self.assertLessEqual(scores[right],scores[left])
        self.assertEqual(scores[(True,True,True)],0)

    def test_overlapping_or_nested_relations_are_not_sequential_evidence(self):
        nested=links_candidate([('temporal','甲后','如果丙，丁。由于戊，己。','甲后，乙。如果丙，丁。由于戊，己。'),
                                ('condition','如果丙','丁','如果丙，丁。')])
        self.assertEqual(self.pair_features(nested),set())
        overlap=links_candidate([('temporal','甲后','如果丙','甲后，乙。如果丙，丁。由于戊，己。'),
                                 ('condition','乙','由于戊，己','乙。如果丙，丁。由于戊，己。')])
        self.assertEqual(self.pair_features(overlap),set())

    def test_duplicate_slot_evidence_cannot_change_the_score(self):
        c=links_candidate(THREE)
        self.choose(dict(candidates=[c]),'synthetic','three-links')
        original=self.rank([c])['synthetic']['grammar_scores_by_family']['link_pair_order']
        modified=deepcopy(c)
        duplicate=deepcopy(modified['grammar']['frames'][0])
        duplicate['end']+=1;duplicate['text']=modified['text'][:duplicate['end']]
        modified['grammar']['frames'].append(duplicate)
        score=self.rank([modified])['synthetic']['grammar_scores_by_family']['link_pair_order']
        self.assertEqual(score,original)

    def test_actual_duplicate_relation_with_padded_scope_preserves_every_rank_score(self):
        self.choose(self.fixture['a']['training'])
        for writer in 'ab':
            pool=deepcopy(self.fixture[writer]['query']['candidates'])
            original=self.rank(pool)
            for candidate in pool:
                duplicate=deepcopy(next(f for f in candidate['grammar']['frames']
                                        if f['kind']=='link' and f['relation']=='temporal'))
                self.assertEqual(candidate['text'][duplicate['end']],'。')
                duplicate['end']+=1
                duplicate['text']=candidate['text'][duplicate['start']:duplicate['end']]
                candidate['grammar']['frames'].append(duplicate)
            revised=self.rank(pool)
            for identifier in original:
                self.assertEqual(revised[identifier]['score'],original[identifier]['score'])
                self.assertEqual(revised[identifier]['grammar_scores_by_family'],original[identifier]['grammar_scores_by_family'])

    def test_scoring_identity_does_not_rewrite_the_saved_annotation_payload(self):
        data=deepcopy(self.fixture['a']['training'])
        first=self.choose(data,event='raw-payload')
        before=self.snapshot()
        c=data['candidates'][0]
        frame=deepcopy(next(f for f in c['grammar']['frames'] if f['kind']=='link' and f['relation']=='temporal'))
        frame['end']+=1;frame['text']=c['text'][frame['start']:frame['end']]
        c['grammar']['frames'].append(frame)
        with self.assertRaises(ValueError):self.choose(data,event='raw-payload')
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.choose(self.fixture['a']['training'],event='raw-payload'),{**first,'replayed':True})

    def test_same_slots_with_conflicting_relation_or_direction_abstain(self):
        candidate=links_candidate(THREE)
        for conflict in ['relation','direction','unknown']:
            changed=deepcopy(candidate)
            duplicate=deepcopy(changed['grammar']['frames'][0])
            duplicate['end']+=1;duplicate['text']=changed['text'][:duplicate['end']]
            if conflict=='relation':duplicate['relation']='cause'
            elif conflict=='unknown':duplicate['relation']='unknown'
            else:
                for slot in duplicate['slots']:slot['role']='head' if slot['role']=='dependent' else 'dependent'
            changed['grammar']['frames'].append(duplicate)
            with self.subTest(conflict=conflict):
                self.assertEqual(self.pair_features(changed),{PREFIX+'condition/dependent>head||cause/dependent>head'})

    def test_same_envelope_with_different_slot_boundaries_remains_distinct_and_safe(self):
        c=links_candidate(THREE)
        alternative={'kind':'link','relation':'unknown','start':0,'end':4,'text':'甲后，乙',
                     'slots':[{'role':'dependent','start':0,'end':1,'text':'甲'},
                              {'role':'head','start':1,'end':4,'text':'后，乙'}]}
        c['grammar']['frames'].append(alternative)
        self.assertEqual(self.pair_features(c),self.pair_features(links_candidate(THREE)))
        validated=learn._validated_candidates([c])[0]
        self.assertEqual(len(learn._grammar_link_identities(validated['grammar'])),4)

    def test_masking_duplicate_relations_preserves_group_uncertainty(self):
        candidate=links_candidate(THREE)
        duplicate=deepcopy(candidate['grammar']['frames'][0])
        duplicate['end']+=1;duplicate['text']=candidate['text'][:duplicate['end']]
        candidate['grammar']['frames'].append(duplicate)
        self.choose(dict(candidates=[candidate]),'synthetic','duplicated-training')
        values={}
        for mask in itertools.product([False,True],repeat=4):
            c=deepcopy(candidate)
            for i,unknown in enumerate(mask):
                if unknown:c['grammar']['frames'][i]['relation']='unknown'
            values[mask]=self.rank([c])['synthetic']['grammar_scores_by_family']['link_pair_order']
        for left,right in itertools.product(values,repeat=2):
            if all(not a or b for a,b in zip(left,right)):
                self.assertLessEqual(values[right],values[left])

    def test_a_single_conflicting_link_identity_cannot_reject_existing_pairs(self):
        full=links_candidate(THREE,identifier='full')
        self.choose(dict(candidates=[full]),'full','train-all')
        conflicted=deepcopy(full);conflicted['id']='conflict'
        conflicted['grammar']['frames']=[deepcopy(full['grammar']['frames'][0])]
        frame=conflicted['grammar']['frames'][0]
        frame['end']=len(conflicted['text']);frame['text']=conflicted['text']
        duplicate=deepcopy(frame);duplicate['end']-=1;duplicate['text']=conflicted['text'][:-1];duplicate['relation']='cause'
        conflicted['grammar']['frames'].append(duplicate)
        replace_atom(conflicted,'己','辛')
        with closing(sqlite3.connect(self.db)) as conn:
            before=conn.execute('SELECT * FROM syntax_prefs WHERE feature LIKE ?',(PREFIX+'%',)).fetchall()
        receipt=self.choose(dict(candidates=[full,conflicted]),'conflict','conflict')
        self.assertFalse(receipt['grammar_negative_feedback'])
        self.assertEqual(receipt['grammar_status'],'abstained')
        self.assertFalse(receipt['link_pair_negative_feedback'])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT * FROM syntax_prefs WHERE feature LIKE ?',(PREFIX+'%',)).fetchall(),before)

    def test_unresolved_pairs_and_mixed_versions_do_not_penalize_old_pairs(self):
        full=links_candidate(THREE,identifier='full')
        pool=dict(candidates=[full])
        self.choose(pool,'full','known')
        with closing(sqlite3.connect(self.db)) as conn:
            before=conn.execute('SELECT * FROM syntax_prefs WHERE feature LIKE ?',(PREFIX+'%',)).fetchall()
        nested=links_candidate([('temporal','甲后','如果丙，丁。由于戊，己。','甲后，乙。如果丙，丁。由于戊，己。'),
                                ('condition','如果丙','丁','如果丙，丁。')],identifier='nested')
        replace_atom(nested,'己','庚')
        result=self.choose(dict(candidates=[full,nested]),'nested','unresolved')
        self.assertTrue(result['grammar_negative_feedback'])
        self.assertFalse(result['link_pair_negative_feedback'])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT * FROM syntax_prefs WHERE feature LIKE ?',(PREFIX+'%',)).fetchall(),before)
        mixed=deepcopy(full);mixed['id']='older';mixed['grammar']['version']=3
        replace_atom(mixed,'己','辛')
        result=self.choose(dict(candidates=[full,mixed]),'older','mixed')
        self.assertFalse(result['link_pair_negative_feedback'])

    def test_missing_unknown_and_off_are_not_implicit_sequence_evidence(self):
        c=links_candidate(THREE)
        for f in c['grammar']['frames']:f['relation']='unknown';f['slots']=[]
        self.assertEqual(self.pair_features(c),set())
        self.choose(self.fixture['b']['training'])
        learn.set_syntax_backend(self.db,'off')
        result=self.rank(self.fixture['b']['query']['candidates'])
        self.assertTrue(all(r['grammar_score']==0 for r in result.values()))
        self.assertTrue(all(r['grammar_scores_by_family']['link_pair_order']==0 for r in result.values()))
        self.assertIn('grammar_link_order',learn.engine_info()['capabilities'])


if __name__=='__main__':unittest.main()
