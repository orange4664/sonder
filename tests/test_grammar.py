"""Grounded grammatical frames generalize beyond rhetorical move labels."""
from contextlib import closing
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('grammar_learn', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(learn)
PREFIX = 'syntax:grammar-v1:writing-model:'


def span(text, fragment):
    start = text.index(fragment)
    return {'start': start, 'end': start + len(fragment), 'text': fragment}


def expression(key, text, form, voice, roles):
    frame = {**span(text, text), 'kind': 'expression', 'form': form, 'voice': voice,
             'slots': [{**span(text, fragment), 'role': role} for role, fragment in roles]}
    return item(key, text, [frame])


def item(key, text, frames):
    return {'id': key, 'label': 'Equal label', 'text': text,
            'grammar': {'version': 1, 'source': 'writing-model',
                        'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'frames': frames}}


def joined(*candidates):
    """Keep independent exact clause evidence when composing a multi-clause probe."""
    frames, offset = [], 0
    for candidate in candidates:
        for original in candidate['grammar']['frames']:
            frame = deepcopy(original)
            for evidence in [frame, *frame['slots']]:
                evidence['start'] += offset
                evidence['end'] += offset
            frames.append(frame)
        offset += len(candidate['text'])
    result = item('multi', ''.join(c['text'] for c in candidates), frames)
    result['grammar']['version'] = max(c['grammar']['version'] for c in candidates)
    return result


class GrammarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sonder-grammar-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.db = str(self.root / 'prefs.db')
        self.path = self.root / 'input.json'
        self.base = patch.object(learn, '_syntax_features', return_value=([], 'heuristic'))
        self.base.start(); self.addCleanup(self.base.stop)
        self.clock = patch.object(learn, '_now_iso', return_value='2026-10-26T00:00:00+00:00')
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.training = [
            expression('verbal', '我们比较了曲线。', 'verbal', 'active', [('agent', '我们'), ('event', '比较'), ('patient', '曲线')]),
            expression('nominal', '我们进行了曲线的比较。', 'nominalized', 'active', [('agent', '我们'), ('event', '比较'), ('patient', '曲线')]),
        ]

    def submit(self, candidates=None, chosen='verbal', event='one'):
        data = {'candidates': self.training if candidates is None else candidates,
                'chosen': chosen, 'round_id': event, 'author_owned': True}
        self.path.write_text(json.dumps(data, ensure_ascii=False))
        return learn.choice_file(self.db, 'methods', 'support', self.path)

    def snapshot(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return list(conn.iterdump())

    def test_nominalization_transfer_reversal_and_context(self):
        probes = [
            expression('nominal', '团队完成了误差的分析。', 'nominalized', 'active', [('agent', '团队'), ('event', '分析'), ('patient', '误差')]),
            expression('verbal', '团队分析了误差。', 'verbal', 'active', [('agent', '团队'), ('event', '分析'), ('patient', '误差')]),
        ]
        for i in range(3): self.submit(event=f'train-{i}')
        ranked = learn.rank_candidates(self.db, 'methods', 'support', probes)
        self.assertEqual(ranked[0]['id'], 'verbal')
        self.assertEqual(ranked[0]['phrase_score'], ranked[1]['phrase_score'])
        self.assertGreater(ranked[0]['grammar_score'], ranked[1]['grammar_score'])
        self.assertTrue(any('verbal' in x['feature'] for x in ranked[0]['matched_grammar_features']))
        self.assertEqual(ranked[0]['grammar_status'], 'complete')
        isolated = learn.rank_candidates(self.db, 'results', 'support', probes)
        self.assertTrue(all(x['score'] == 0 for x in isolated))
        for i in range(5): self.submit(probes, 'nominal', f'reverse-{i}')
        self.assertEqual(learn.rank_candidates(self.db, 'methods', 'support', probes)[0]['id'], 'nominal')

    def test_voice_transfer_is_not_a_lexical_template(self):
        active = expression('a', '研究人员检测薄膜。', 'verbal', 'active', [('agent', '研究人员'), ('event', '检测'), ('patient', '薄膜')])
        passive = expression('p', '薄膜被研究人员检测。', 'verbal', 'passive', [('agent', '研究人员'), ('event', '检测'), ('patient', '薄膜')])
        for i in range(3): self.submit([active, passive], 'a', f'voice-{i}')
        probes = [expression('p', '传感器由专家校准。', 'verbal', 'passive', [('agent', '专家'), ('event', '校准'), ('patient', '传感器')]),
                  expression('a', '专家校准传感器。', 'verbal', 'active', [('agent', '专家'), ('event', '校准'), ('patient', '传感器')])]
        rows = learn.rank_candidates(self.db, 'methods', 'support', probes)
        self.assertEqual(rows[0]['id'], 'a')
        self.assertGreater(rows[0]['grammar_score'], rows[1]['grammar_score'])

    def test_relative_link_adds_preference_beyond_shared_expression(self):
        def relative(key, actor, event, dep, noun):
            text = f'{actor}{event}了{dep}{noun}。'
            c = expression(key, text, 'verbal', 'active', [('agent', actor), ('event', event), ('patient', dep + noun)])
            c['grammar']['frames'].append({**span(text, dep + noun), 'kind': 'link', 'relation': 'relative',
                                           'slots': [{**span(text, dep), 'role': 'dependent'}, {**span(text, noun), 'role': 'head'}]})
            return c
        train = relative('relative', '我们', '分析', '经过校准的', '信号')
        self.submit([train], 'relative')
        probe = relative('relative', '团队', '检查', '经过筛选的', '样本')
        plain = expression('plain', '样本经过筛选，团队检查了这些样本。', 'verbal', 'active',
                           [('agent', '团队'), ('event', '检查'), ('patient', '这些样本')])
        rows = learn.rank_candidates(self.db, 'methods', 'support', [plain, probe])
        self.assertEqual(rows[0]['id'], 'relative')
        self.assertGreater(rows[0]['grammar_score'], rows[1]['grammar_score'])
        self.assertGreater(rows[0]['grammar_scores_by_family']['link'], 0)
        self.assertEqual(rows[1]['grammar_scores_by_family']['link'], 0)
        # A link cannot claim invalid/missing slots, cross its frame, or leave
        # the whole sentence uncovered when no root frame is supplied.
        bad = deepcopy(probe); bad['grammar']['frames'] = bad['grammar']['frames'][1:]
        with self.assertRaisesRegex(ValueError, 'grammar'):
            self.submit([bad], 'relative', 'uncovered')
        bad = deepcopy(probe); bad['grammar']['frames'][1]['slots'].pop()
        with self.assertRaisesRegex(ValueError, 'grammar'):
            self.submit([bad], 'relative', 'missing-head')

    def test_condition_order_survives_new_wording(self):
        def condition(key, dependent, head, first):
            text = '，'.join([dependent, head] if first else [head, dependent]) + '。'
            frame = {**span(text, text), 'kind': 'link', 'relation': 'condition',
                     'slots': [{**span(text, dependent), 'role': 'dependent'}, {**span(text, head), 'role': 'head'}]}
            return item(key, text, [frame])
        pair = [condition('first', '若温度恒定', '信号保持稳定', True), condition('last', '若温度恒定', '信号保持稳定', False)]
        self.submit(pair, 'first', 'condition-training')
        probes = [condition('last', '如果压力不变', '流量保持恒定', False), condition('first', '如果压力不变', '流量保持恒定', True)]
        rows = learn.rank_candidates(self.db, 'methods', 'support', probes)
        self.assertEqual(rows[0]['id'], 'first')
        self.assertEqual(rows[0]['phrase_score'], rows[1]['phrase_score'])
        self.assertGreater(rows[0]['grammar_score'], rows[1]['grammar_score'])

    def test_invalid_frames_slots_or_stale_text_fail_before_db_open(self):
        cases = []
        def altered(change):
            c = deepcopy(self.training[0]); change(c); cases.append(c)
        altered(lambda c: c.update(grammar=None))
        altered(lambda c: c['grammar'].update(version=True))
        altered(lambda c: c['grammar'].update(source='invented'))
        altered(lambda c: c['grammar'].update(text_sha256='0' * 64))
        altered(lambda c: c['grammar'].update(extra=1))
        altered(lambda c: c['grammar'].update(frames=[{}] * 17))
        altered(lambda c: c.update(text=c['text'] + '可能存在偏差。'))
        for field, value in [('start', True), ('end', 999), ('text', '旧正文'), ('form', 'magic'),
                             ('voice', []), ('kind', 'magic'), ('extra', 1)]:
            altered(lambda c, f=field, v=value: c['grammar']['frames'][0].update({f: v}))
        altered(lambda c: c['grammar']['frames'][0].update(slots=[]))
        altered(lambda c: c['grammar']['frames'][0]['slots'].append(deepcopy(c['grammar']['frames'][0]['slots'][0])))
        altered(lambda c: c['grammar']['frames'].append(deepcopy(c['grammar']['frames'][0])))
        altered(lambda c: c['grammar']['frames'][0]['slots'][0].update(role='magic'))
        altered(lambda c: c['grammar']['frames'][0]['slots'][0].update(start=-1))
        for c in cases:
            with self.subTest(c=c):
                with self.assertRaisesRegex(ValueError, 'grammar'):
                    self.submit([c])
                self.assertFalse(Path(self.db).exists())
                with self.assertRaisesRegex(ValueError, 'grammar'):
                    learn.rank_candidates(self.db, 'methods', 'support', [c])
                self.assertFalse(Path(self.db).exists())

    def test_missing_and_unknown_grammar_are_not_negative_evidence(self):
        self.submit()
        with closing(sqlite3.connect(self.db)) as conn:
            before = conn.execute("SELECT * FROM syntax_prefs WHERE feature LIKE 'syntax:grammar-%'").fetchall()
        missing = deepcopy(self.training); del missing[1]['grammar']
        result = self.submit(missing, 'nominal', 'missing')
        self.assertFalse(result['grammar_negative_feedback'])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT * FROM syntax_prefs WHERE feature LIKE 'syntax:grammar-%'").fetchall(), before)
        unknown = item('unknown', '该量与前者有关。', [])
        row = learn.rank_candidates(self.db, 'methods', 'support', [unknown])[0]
        self.assertEqual(row['grammar_status'], 'abstained')
        self.assertEqual(row['grammar_score'], 0)

    def test_unknown_form_does_not_gain_score_by_omitting_unmatched_features(self):
        for i in range(3): self.submit(event=f'train-{i}')
        known = deepcopy(self.training[1])
        partial = deepcopy(known); partial['grammar']['frames'][0]['form'] = 'unknown'
        known_row = learn.rank_candidates(self.db, 'methods', 'support', [known])[0]
        partial_row = learn.rank_candidates(self.db, 'methods', 'support', [partial])[0]
        self.assertEqual(partial_row['grammar_status'], 'partial')
        self.assertLessEqual(partial_row['grammar_score'], known_row['grammar_score'])

    def test_multiframe_form_and_voice_uncertainty_cannot_increase_score(self):
        for i in range(3): self.submit(event=f'known-{i}')
        passive = expression('passive', '曲线由团队比较。', 'verbal', 'passive',
                             [('patient', '曲线'), ('agent', '团队'), ('event', '比较')])
        for field, extra in [('form', self.training[1]), ('voice', passive)]:
            known = joined(self.training[0], extra)
            before = learn.rank_candidates(self.db, 'methods', 'support', [known])[0]
            # Three complete verbal/active choices; these mixed known forms
            # retain the established complete-input score after the fix.
            self.assertEqual(before['grammar_score'], 2812.5)
            uncertain = deepcopy(known)
            uncertain['grammar']['frames'][1][field] = 'unknown'
            after = learn.rank_candidates(self.db, 'methods', 'support', [uncertain])[0]
            with self.subTest(field=field):
                self.assertEqual(after['grammar_status'], 'partial')
                self.assertEqual(after['phrase_score'], before['phrase_score'])
                self.assertLessEqual(after['grammar_score'], before['grammar_score'])

    def test_additional_unknown_frames_remain_zero_evidence(self):
        for i in range(3): self.submit(event=f'known-{i}')
        state = expression('state', '响应是稳定的。', 'copular', 'nonagentive',
                           [('patient', '响应'), ('event', '是'), ('complement', '稳定的')])
        state['grammar']['version'] = 2
        known = joined(self.training[0], self.training[1], state)
        for order in [(1, 2), (2, 1)]:
            candidate = deepcopy(known)
            previous = learn.rank_candidates(self.db, 'methods', 'support', [candidate])[0]['grammar_score']
            for frame in order:
                candidate['grammar']['frames'][frame]['form'] = 'unknown'
                current = learn.rank_candidates(self.db, 'methods', 'support', [candidate])[0]['grammar_score']
                with self.subTest(order=order, frame=frame):
                    self.assertLessEqual(current, previous)
                previous = current

    def test_multilink_unknown_relation_cannot_improve_rank(self):
        def linked(key, text, relation, dependent, head):
            return item(key, text, [{**span(text, text), 'kind': 'link', 'relation': relation,
                'slots': [{**span(text, dependent), 'role': 'dependent'},
                          {**span(text, head), 'role': 'head'}]}])
        condition = linked('condition', '若温度恒定，信号保持稳定。', 'condition', '若温度恒定', '信号保持稳定')
        concession = linked('concession', '虽然压力变化，响应仍然稳定。', 'concession', '虽然压力变化', '响应仍然稳定')
        self.submit([condition], 'condition', 'learn-condition')
        known = joined(condition, concession)
        before = learn.rank_candidates(self.db, 'methods', 'support', [known])[0]
        self.assertEqual(before['grammar_scores_by_family']['link'], 750)
        uncertain = deepcopy(known)
        uncertain['grammar']['frames'][1]['relation'] = 'unknown'
        after = learn.rank_candidates(self.db, 'methods', 'support', [uncertain])[0]
        self.assertLessEqual(after['grammar_score'], before['grammar_score'])
        snapshot = self.snapshot()
        learn.rank_candidates(self.db, 'methods', 'support', [uncertain])
        self.assertEqual(self.snapshot(), snapshot)

    def test_grammar_is_part_of_receipt_but_slot_presentation_order_is_not(self):
        first = self.submit(); before = self.snapshot()
        reordered = deepcopy(self.training)
        for c in reordered: c['grammar']['frames'][0]['slots'].reverse()
        self.assertEqual(self.submit(reordered), {**first, 'replayed': True})
        self.assertEqual(self.snapshot(), before)
        changed = deepcopy(self.training); changed[0]['grammar']['frames'][0]['voice'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'round_id'): self.submit(changed)
        self.assertEqual(self.snapshot(), before)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT text,rewards FROM exemplars').fetchone(), (self.training[0]['text'], 1))

    def test_grammar_write_failure_rolls_back_the_entire_choice(self):
        learn.list_exemplars(self.db)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("CREATE TRIGGER fail_grammar BEFORE INSERT ON syntax_prefs "
                         "WHEN NEW.feature LIKE 'syntax:grammar-%' BEGIN SELECT RAISE(ABORT,'grammar failed'); END")
        before = self.snapshot()
        with self.assertRaises(sqlite3.DatabaseError): self.submit()
        self.assertEqual(self.snapshot(), before)

    def test_off_disables_grammar_without_silently_accepting_invalid_evidence(self):
        self.base.stop()
        learn.set_syntax_backend(self.db, 'off')
        receipt = self.submit()
        self.assertEqual(receipt['grammar_features'], 0)
        row = learn.rank_candidates(self.db, 'methods', 'support', self.training)[0]
        self.assertEqual(row['grammar_status'], 'disabled')
        self.assertEqual(row['grammar_score'], 0)
        bad = deepcopy(self.training); bad[0]['grammar']['text_sha256'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'grammar'): self.submit(bad, event='bad')

    def test_documented_grammar_example_runs_in_real_cli_and_replays(self):
        doc = (ROOT / 'references/grammar-frames.md').read_text()
        for marker in ('sonder-grammar-example', 'sonder-state-grammar-example'):
            with self.subTest(marker=marker):
                code = doc.split(f'<!-- {marker} -->', 1)[1].split('```python\n', 1)[1].split('```', 1)[0]
                c = json.loads(subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True, check=True).stdout)
                self.path.write_text(json.dumps({'round_id': marker, 'candidates': [c], 'chosen': c['id'], 'author_owned': True}, ensure_ascii=False))
                command = [sys.executable, '-I', str(ROOT / 'library/learn.py'), 'choice-file', self.db, 'methods', 'support', str(self.path)]
                first = json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)
                before = self.snapshot()
                replay = json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)
                self.assertGreater(first['grammar_features'], 0)
                self.assertEqual(first['grammar_input_version'], 3)
                self.assertEqual(replay, {**first, 'replayed': True})
                self.assertEqual(self.snapshot(), before)

    def state_pair(self, entity, adjective):
        direct = expression('direct', entity + adjective + '。', 'adjectival', 'nonagentive',
                            [('patient', entity), ('event', adjective)])
        copular = expression('copular', entity + '是' + adjective + '的。', 'copular', 'nonagentive',
                             [('patient', entity), ('event', '是'), ('complement', adjective + '的')])
        for c in (direct, copular): c['grammar']['version'] = 2
        return [direct, copular]

    def test_state_forms_transfer_to_new_words_without_forgetting_old_grammar(self):
        self.submit()  # Existing verbal preference stays readable after extension.
        with closing(sqlite3.connect(self.db)) as conn:
            old_rows = conn.execute('SELECT * FROM syntax_prefs ORDER BY feature').fetchall()
        pair = self.state_pair('样本', '稳定')
        for i in range(3): self.submit(pair, 'direct', f'state-{i}')
        probes = self.state_pair('信号', '清晰')[::-1]
        rows = learn.rank_candidates(self.db, 'methods', 'support', probes)
        self.assertEqual(rows[0]['id'], 'direct')
        self.assertEqual(rows[0]['phrase_score'], rows[1]['phrase_score'])
        self.assertGreater(rows[0]['grammar_score'], rows[1]['grammar_score'])
        self.assertEqual(rows[0]['grammar_input_version'], 2)
        self.assertEqual(rows[0]['grammar_feature_version'], 'v1')
        self.assertEqual(rows[0]['grammar_status'], 'complete')
        with closing(sqlite3.connect(self.db)) as conn:
            current = {row[1]: row for row in conn.execute('SELECT * FROM syntax_prefs')}
        for row in old_rows:
            # Only the shared nonagentive voice could overlap; old training was active.
            self.assertEqual(current[row[1]], row)

    def test_v2_copula_requires_complement_and_v1_keeps_original_contract(self):
        pair = self.state_pair('样本', '稳定')
        for item in pair:
            old = deepcopy(item); old['grammar']['version'] = 1
            with self.assertRaisesRegex(ValueError, 'grammar'):
                self.submit([old], old['id'])
            self.assertFalse(Path(self.db).exists())
        copula = deepcopy(pair[1]); copula['grammar']['frames'][0]['slots'].pop()
        with self.assertRaisesRegex(ValueError, 'complement'):
            self.submit([copula], 'copular')
        self.assertFalse(Path(self.db).exists())
        for version in (True, 0, 99, '2'):
            bad = deepcopy(pair[0]); bad['grammar']['version'] = version
            with self.assertRaisesRegex(ValueError, 'grammar'):
                self.submit([bad], 'direct')
            self.assertFalse(Path(self.db).exists())

    def test_protocol_upgrade_preserves_features_but_not_decision_identity(self):
        first = self.submit(); before = self.snapshot()
        new = deepcopy(self.training)
        for c in new: c['grammar']['version'] = 2
        old_rank = learn.rank_candidates(self.db, 'methods', 'support', self.training)
        new_rank = learn.rank_candidates(self.db, 'methods', 'support', new)
        self.assertEqual([(x['id'], x['score'], x['matched_grammar_features']) for x in old_rank],
                         [(x['id'], x['score'], x['matched_grammar_features']) for x in new_rank])
        with self.assertRaisesRegex(ValueError, 'round_id'):
            self.submit(new)
        self.assertEqual(self.submit(), {**first, 'replayed': True})
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__': unittest.main()
