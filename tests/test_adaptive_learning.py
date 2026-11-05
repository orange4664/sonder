"""Production D regression: frozen real pools, transactional state and old API."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('adaptive_test_engine', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)
FIXTURE = json.loads((ROOT / 'tests/fixtures/adaptive-learning.json').read_text())
NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


class AdaptiveLearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sonder-adaptive-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = str(self.root / 'preference.db')
        self.data = deepcopy(next(iter(FIXTURE['decisions'].values())))
        self.context = self.data['learning_context']

    def raw(self, sql, args=()):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute(sql, args).fetchall()

    def dump(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return list(conn.iterdump())

    def choice(self, data=None, at=NOW):
        data = data or self.data
        path = self.root / 'decision.json'
        path.write_text(json.dumps(data, ensure_ascii=False))
        with patch.object(learn, '_now_iso', return_value=at.isoformat()):
            return learn.choice_file(self.db, **data['learning_context'], path=path)

    def rank(self, data=None, at=NOW):
        data = data or self.data
        with patch.object(learn, '_now_iso', return_value=at.isoformat()):
            return learn.rank_candidates(self.db, **data['learning_context'], candidates=data['candidates'],
                **({'text_format': data['text_format']} if 'text_format' in data else {}))

    def extract(self, data):
        candidates = learn._validated_candidates(data['candidates'])
        extracted = {}
        for candidate in candidates:
            diagnostics = {}
            options = {'text_format': data['text_format']} if 'text_format' in data else {}
            syntax, backend, analysis, grammar = learn._candidate_syntax_features(candidate, self.db, diagnostics, **options)
            extracted[candidate['text']] = (set(learn._phrase_features(candidate['text'], **options)),
                set(syntax), backend, set(analysis), set(grammar), diagnostics)
        return candidates, extracted

    def test_vector_matches_every_individual_legacy_score_coefficient(self):
        families = set()
        # A basis-weight oracle exercises the actual rank implementation, rather
        # than restating its denominators in a second expected-value function.
        for data in FIXTURE['pools'].values():
            candidates, extracted = self.extract(data)
            for candidate in candidates:
                vector = learn._adaptive_vector(candidate, extracted[candidate['text']])
                for feature, coefficient in vector.items():
                    families.add(learn._adaptive_feature_family(feature))
                    with patch.object(learn, '_phrase_weight_of', side_effect=lambda c, x, f, n: (1_000_000 if 'phrase|' + f == feature else 0, 1)), \
                         patch.object(learn, '_syntax_weight_of', side_effect=lambda c, x, f, n: (1_000_000 if 'syntax|' + f == feature else 0, 1)):
                        row = self.rank({**data, 'candidates': [candidate]})[0]
                    self.assertAlmostEqual(row['score'] / 1_000_000, coefficient, places=11)
        self.assertEqual(families, {'phrase', 'base', 'analysis', 'grammar'})

    def test_partial_and_mixed_backend_sources_cannot_invent_comparisons(self):
        candidates, extracted = self.extract(self.data)
        chosen = next(c for c in candidates if c['id'] == self.data['chosen'])
        original_vector, original_differences = learn._adaptive_prepare(chosen, candidates, extracted)
        self.assertTrue(original_differences)
        for mode in ['off', 'mixed-fingerprint', 'partial-analysis', 'partial-grammar']:
            with self.subTest(mode=mode):
                pool, values = deepcopy(candidates), deepcopy(extracted)
                if mode in ['off', 'mixed-fingerprint']:
                    for index, c in enumerate(pool):
                        old = values[c['text']]
                        values[c['text']] = (old[0], set() if mode == 'off' else old[1],
                            'off' if mode == 'off' else 'stanza', set() if mode == 'off' else old[3],
                            set() if mode == 'off' else old[4], {'model_fingerprint': str(index)})
                else:
                    for c in pool:
                        if mode == 'partial-analysis':
                            c['analysis']['units'][0]['move'] = 'unknown'
                        else:
                            c['grammar']['frames'][0]['form'] = 'unknown'
                picked = next(c for c in pool if c['id'] == chosen['id'])
                vector, differences = learn._adaptive_prepare(picked, pool, values)
                keys = {key for diff in differences for key in diff}
                forbidden = {'off': {'base', 'analysis', 'grammar'}, 'mixed-fingerprint': {'base'},
                    'partial-analysis': {'analysis'}, 'partial-grammar': {'grammar'}}[mode]
                self.assertFalse({learn._adaptive_feature_family(k) for k in keys} & forbidden)
                if mode == 'off':
                    self.assertTrue(all(k.startswith('phrase|') for k in vector))
                else:
                    self.assertEqual(vector.keys(), original_vector.keys())

    def test_older_grammar_version_does_not_compare_unrepresentable_recipient(self):
        drafts = []
        for cid, text, recipient in [('new', 'We told the operator the result.', 'the operator'),
                                      ('old', 'We gave the result to the operator.', None)]:
            slots = [{'role': 'agent', 'text': 'We'}, {'role': 'event', 'text': 'told' if cid == 'new' else 'gave'},
                     {'role': 'patient', 'text': 'the result'}]
            if recipient:
                slots.append({'role': 'recipient', 'text': recipient})
            drafts.append({'id': cid, 'text': text, 'analysis': {'units': []},
                'grammar': {'version': 5 if recipient else 4, 'frames': [dict(kind='expression', text=text,
                form='verbal', voice='active', slots=slots)]}})
        data = learn.bind_candidate_data({'round_id': 'recipient', 'candidates': drafts})
        candidates, extracted = self.extract(data)
        for chosen in candidates:
            _, differences = learn._adaptive_prepare(chosen, candidates, extracted)
            self.assertTrue(differences)
            self.assertFalse(any(learn._is_argument_shape(k[7:]) for diff in differences for k in diff if k.startswith('syntax|')))

    def test_snapshot_failure_is_atomic_and_query_does_not_initialize(self):
        self.choice()
        before = self.dump()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("CREATE TRIGGER fail_snapshot BEFORE INSERT ON adaptive_legacy_mass BEGIN SELECT RAISE(ABORT,'snapshot fault'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'snapshot fault'):
            learn.learning_policy(self.db, 'ensure-adaptive')
        self.assertFalse(learn.learning_policy(self.db)['configured'])
        self.assertEqual(self.raw('SELECT COUNT(*) FROM adaptive_legacy'), [(0,)])
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('DROP TRIGGER fail_snapshot')
        self.assertEqual(before, self.dump())

    def test_policy_weights_and_labels_share_one_rank_snapshot(self):
        self.choice()
        learn.learning_policy(self.db, 'adaptive-v1')
        self.data['round_id'] += '-adaptive'
        self.choice()
        before = self.rank()
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
        original = learn._adaptive.policy
        called = False
        def during(conn):
            nonlocal called
            result = original(conn)
            if not called:
                called = True
                with closing(sqlite3.connect(self.db, timeout=0)) as writer, writer:
                    writer.execute("UPDATE adaptive_settings SET value='legacy' WHERE key='policy'")
                    writer.execute('UPDATE adaptive_weights SET weight=weight*2')
                    writer.execute('UPDATE adaptive_legacy SET weight=weight*2')
                    writer.execute('UPDATE prefs SET weight=weight*2')
            return result
        with patch.object(learn._adaptive, 'policy', side_effect=during):
            ranked = self.rank()
        self.assertTrue(called)
        self.assertEqual(before, ranked)
        self.assertTrue(all(r['scoring_version'] == 2 for r in self.rank()))

    def test_off_removes_structural_scores_and_new_evidence(self):
        learn.learning_policy(self.db, 'adaptive-v1')
        self.choice()
        learn.set_syntax_backend(self.db, 'off')
        self.data['round_id'] += '-off'
        result = self.choice()
        self.assertEqual(result['adaptive']['contrast_feature_counts']['grammar'], 0)
        self.assertEqual(result['adaptive']['contrast_feature_counts']['base'], 0)
        for row in self.rank():
            self.assertEqual(row['syntax_score'], 0)
        candidates, extracted = self.extract(self.data)
        for c in candidates:
            self.assertTrue(all(k.startswith('phrase|') for k in learn._adaptive_vector(c, extracted[c['text']])))

    def test_old_profile_fades_then_fresh_acceptance_matches_clean_store(self):
        learn.learning_policy(self.db, 'adaptive-v1')
        self.data.pop('edit_scope', None)
        self.data['candidates'] = [c for c in self.data['candidates'] if c['id'] == self.data['chosen']]
        for index in range(12):
            self.data['round_id'] = 'old-' + str(index)
            self.choice(at=NOW - timedelta(days=2000))
        self.data['round_id'] = 'fresh'
        self.choice()
        old = self.raw("SELECT feature,weight FROM adaptive_weights WHERE channel='profile' ORDER BY feature")
        self.db = str(self.root / 'clean.db')
        learn.learning_policy(self.db, 'adaptive-v1')
        self.choice()
        fresh = self.raw("SELECT feature,weight FROM adaptive_weights WHERE channel='profile' ORDER BY feature")
        self.assertEqual([k for k, _ in old], [k for k, _ in fresh])
        for (_, a), (_, b) in zip(old, fresh):
            self.assertAlmostEqual(a, b, places=14)

    def test_concurrent_extraction_commits_match_actual_serial_order_and_clock(self):
        learn.learning_policy(self.db, 'adaptive-v1')
        slow = deepcopy(self.data)
        slow['round_id'] = 'slow'
        fast = deepcopy(self.data)
        fast['round_id'] = 'fast'
        fast['chosen'] = next(c['id'] for c in fast['candidates'] if c['id'] != slow['chosen'])
        entered, release = threading.Event(), threading.Event()
        errors, clocks = [], []
        parse = learn._candidate_syntax_features
        def stalled(*args, **kwargs):
            if threading.current_thread().name == 'slow-parser' and not entered.is_set():
                entered.set()
                if not release.wait(10):
                    raise RuntimeError('parser timeout')
            return parse(*args, **kwargs)
        def now():
            value = (NOW + timedelta(seconds=len(clocks))).isoformat()
            clocks.append(value)
            return value
        def apply(data):
            value, candidates = learn._validated_candidate_data(data)
            return learn._choice_data(self.db, **data['learning_context'], data=value, candidates=candidates)
        def worker():
            try:
                apply(slow)
            except BaseException as error:
                errors.append(error)
        with patch.object(learn, '_candidate_syntax_features', side_effect=stalled), \
             patch.object(learn, '_now_iso', side_effect=now):
            thread = threading.Thread(target=worker, name='slow-parser')
            thread.start()
            try:
                self.assertTrue(entered.wait(10))
                apply(fast)
            finally:
                release.set()
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(clocks, [NOW.isoformat(), (NOW + timedelta(seconds=1)).isoformat()])
        self.assertEqual([r[0] for r in self.raw('SELECT round_id FROM choice_events ORDER BY rowid')], ['fast', 'slow'])
        state = self.raw('SELECT * FROM adaptive_weights ORDER BY channel,feature')
        self.db = str(self.root / 'serial.db')
        learn.learning_policy(self.db, 'adaptive-v1')
        self.choice(fast, NOW)
        self.choice(slow, NOW + timedelta(seconds=1))
        self.assertEqual(state, self.raw('SELECT * FROM adaptive_weights ORDER BY channel,feature'))

    def test_summary_and_large_labels_preserve_adaptive_text_scores(self):
        learn.learning_policy(self.db, 'adaptive-v1')
        self.choice()
        before = self.rank()
        loser = before[-1]
        self.assertLess(loser['score'], before[0]['score'])
        learn.global_pref(self.db, loser['label'], 100_000_000)
        after = self.rank()
        self.assertEqual([(r['id'], r['score']) for r in before], [(r['id'], r['score']) for r in after])
        summary = learn._rank_summary(after)
        for full, short in zip(after, summary):
            for field in ['score', 'scoring_version', 'learning_policy', 'comparison_score', 'profile_score', 'legacy_prior_score']:
                self.assertEqual(full[field], short[field])

    def test_same_chosen_text_is_not_an_opponent_and_equal_differences_deduplicate(self):
        learn.learning_policy(self.db, 'adaptive-v1')
        self.data.pop('edit_scope', None)
        chosen = next(c for c in self.data['candidates'] if c['id'] == self.data['chosen'])
        self.data['candidates'] = [chosen, {**deepcopy(chosen), 'id': 'same-text'}]
        receipt = self.choice()
        self.assertEqual(receipt['adaptive']['comparative_differences'], 0)
        self.assertEqual(self.raw("SELECT COUNT(*) FROM adaptive_weights WHERE channel='comparison'"), [(0,)])
        # Distinct full texts with the same extracted vector supply one contrast.
        pool = [dict(id='a', label='a', text='one'), dict(id='b', label='b', text='two'), dict(id='c', label='c', text='TWO')]
        data = {**self.data, 'round_id': 'distinct', 'chosen': 'a', 'candidates': pool}
        result = self.choice(data)
        self.assertEqual(result['adaptive']['comparative_differences'], 1)

    def test_missing_or_partial_adaptive_module_fails_capability_check(self):
        library = self.root / 'library'
        library.mkdir()
        (library / 'learn.py').write_bytes((ROOT / 'library/learn.py').read_bytes())
        for partial in [None, 'SCORING_VERSION=3\nPOLICIES=("legacy", "adaptive-v1")\n']:
            if partial:
                (library / 'adaptive_learning.py').write_text(partial)
            proc = subprocess.run([sys.executable, '-I', str(library / 'learn.py'), 'engine-info'],
                cwd=self.root, capture_output=True, text=True, timeout=20)
            self.assertNotEqual(proc.returncode, 0)
            self.assertNotIn('"adaptive_preference_learning"', proc.stdout)

    def test_policy_is_explicit_default_and_ensure_respects_legacy(self):
        status = learn.learning_policy(self.db)
        self.assertEqual(status, dict(policy='legacy', configured=False, scoring_version=2,
                                     adaptive_initialized=False, changed=False))
        rows = self.rank()
        self.assertTrue(all(r['scoring_version'] == 2 and 'learning_policy' not in r for r in rows))
        self.assertTrue(learn.learning_policy(self.db, 'legacy')['changed'])
        self.assertEqual(learn.learning_policy(self.db, 'ensure-adaptive')['policy'], 'legacy')
        self.assertFalse(learn.learning_policy(self.db, 'legacy')['changed'])
        self.assertFalse(learn.learning_policy(self.db)['adaptive_initialized'])
        with self.assertRaises(ValueError):
            learn.learning_policy(self.db, 'unknown')

    def test_snapshot_once_preserves_raw_age_and_never_captures_future_updates(self):
        self.choice()
        old = self.raw('SELECT context,feature,weight,last_at FROM phrase_prefs')
        before = self.dump()
        status = learn.learning_policy(self.db, 'ensure-adaptive')
        self.assertTrue(status['changed'] and status['adaptive_initialized'])
        snapshot = self.raw('SELECT context,feature,weight,last_at FROM adaptive_legacy ORDER BY feature')
        self.assertTrue(all((c, 'phrase|' + f, w, t) in snapshot for c, f, w, t in old))
        raw_mass = self.raw('SELECT context,raw_mass FROM adaptive_legacy_mass')
        self.assertEqual(sum(x[2] for x in snapshot), sum(x[1] for x in raw_mass))
        self.data['round_id'] += '-new'
        self.choice()
        for action in ['adaptive-v1', 'legacy', 'adaptive-v1', 'ensure-adaptive']:
            learn.learning_policy(self.db, action)
        self.assertEqual(snapshot, self.raw('SELECT context,feature,weight,last_at FROM adaptive_legacy ORDER BY feature'))
        self.assertEqual(raw_mass, self.raw('SELECT context,raw_mass FROM adaptive_legacy_mass'))
        self.assertTrue(any('choice_events' in row for row in before))
        self.assertEqual(self.raw('SELECT COUNT(*) FROM choice_events'), [(2,)])

    def test_schema5_upgrade_failure_rolls_back_new_tables_and_version(self):
        with closing(learn._open(self.db)):
            pass
        with closing(sqlite3.connect(self.db)) as conn, conn:
            for name in ['adaptive_settings', 'adaptive_weights', 'adaptive_legacy', 'adaptive_legacy_mass', 'adaptive_opposition']:
                conn.execute('DROP TABLE IF EXISTS ' + name)
            conn.execute('PRAGMA user_version=5')
        before = self.dump()
        real_connect = sqlite3.connect
        class Broken(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql == f'PRAGMA user_version = {learn.SCHEMA_VERSION}':
                    raise RuntimeError('migration fault')
                return super().execute(sql, *args, **kwargs)
        with patch.object(learn.sqlite3, 'connect', side_effect=lambda *a, **kw: real_connect(*a, factory=Broken, **kw)):
            with self.assertRaisesRegex(RuntimeError, 'migration fault'):
                learn.learning_policy(self.db)
        self.assertEqual(before, self.dump())
        self.assertEqual(learn.learning_policy(self.db)['policy'], 'legacy')
        self.assertEqual(self.raw('PRAGMA user_version'), [(learn.SCHEMA_VERSION,)])

    def test_old_receipt_and_optout_do_not_extract_or_learn_after_enable(self):
        original = self.choice()
        learn.learning_policy(self.db, 'adaptive-v1')
        before = self.dump()
        with patch.object(learn, '_candidate_syntax_features', side_effect=AssertionError('extraction')):
            self.assertEqual(self.choice(), {**original, 'replayed': True})
        self.assertEqual(before, self.dump())
        self.data.update(round_id='optout', learn_from_choice=False)
        with patch.object(learn, '_candidate_syntax_features', side_effect=AssertionError('extraction')):
            result = self.choice()
        self.assertFalse(result['feedback_applied'])
        self.assertNotIn('adaptive', result)
        self.assertEqual(self.raw('SELECT COUNT(*) FROM adaptive_weights'), [(0,)])

    def test_single_author_observation_is_bounded_and_replay_is_exact(self):
        learn.learning_policy(self.db, 'adaptive-v1')
        self.data.pop('edit_scope', None)
        self.data['candidates'] = [c for c in self.data['candidates'] if c['id'] == self.data['chosen']]
        for index in range(24):
            self.data['round_id'] = 'single-' + str(index)
            result = self.choice()
        self.assertEqual(result['adaptive']['comparative_differences'], 0)
        self.assertGreater(result['adaptive']['accepted_features'], 0)
        self.assertLessEqual(result['adaptive']['profile_mass'], 1.0 + 1e-12)
        self.assertEqual(self.raw("SELECT COUNT(*) FROM adaptive_weights WHERE channel='comparison'"), [(0,)])
        self.assertEqual(self.raw('SELECT SUM(rewards) FROM exemplars'), [(24,)])
        before = self.dump()
        self.assertEqual(self.choice(), {**result, 'replayed': True})
        self.assertEqual(before, self.dump())

    def test_duplicate_text_order_and_labels_do_not_change_model(self):
        variants = [deepcopy(self.data) for _ in range(3)]
        variants[1]['candidates'] = list(reversed(variants[1]['candidates']))
        variants[2].pop('edit_scope', None)
        for index, candidate in enumerate(list(variants[2]['candidates'])):
            candidate['label'] = 'renamed-' + str(index)
            variants[2]['candidates'].append({**candidate, 'id': 'duplicate-' + candidate['id']})
        states = []
        for index, data in enumerate(variants):
            self.db = str(self.root / (str(index) + '.db'))
            learn.learning_policy(self.db, 'adaptive-v1')
            self.choice(data)
            states.append(self.raw('SELECT * FROM adaptive_weights ORDER BY channel,feature'))
        self.assertEqual(states[0], states[1])
        self.assertEqual(states[0], states[2])

    def test_profile_and_comparison_decay_without_read_normalization(self):
        learn.learning_policy(self.db, 'adaptive-v1')
        self.choice()
        before = self.dump()
        current = {r['id']: r for r in self.rank()}
        later = self.rank(at=NOW + timedelta(days=30))
        for row in later:
            for field in ['comparison_score', 'profile_score', 'score']:
                self.assertAlmostEqual(row[field], current[row['id']][field] / 2, places=13)
        self.assertEqual(before, self.dump())

    def test_feedback_failure_rolls_back_compatibility_model_exemplar_and_receipt(self):
        learn.learning_policy(self.db, 'adaptive-v1')
        for table in ['adaptive_weights', 'choice_events']:
            with self.subTest(table=table):
                with closing(sqlite3.connect(self.db)) as conn, conn:
                    conn.execute(f"CREATE TRIGGER fail_write BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'injected'); END")
                before = self.dump()
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected'):
                    self.choice()
                self.assertEqual(before, self.dump())
                with closing(sqlite3.connect(self.db)) as conn, conn:
                    conn.execute('DROP TRIGGER fail_write')
        self.choice()
        self.assertEqual(self.raw('SELECT COUNT(*) FROM choice_events'), [(1,)])

    def test_frozen_actual_pools_equal_independently_checked_D(self):
        for index, track in enumerate(FIXTURE['tracks']):
            with self.subTest(track=track['name']):
                self.db = str(self.root / ('track-' + str(index) + '.db'))
                with closing(learn._open(self.db)) as conn, conn:
                    for context, feature, weight, last_at in track['legacy']:
                        table, key = ('phrase_prefs', feature[7:]) if feature.startswith('phrase|') else ('syntax_prefs', feature[7:])
                        conn.execute(f'INSERT INTO {table} VALUES(?,?,?,?,?)', (context, key, weight, 1, last_at))
                learn.learning_policy(self.db, 'adaptive-v1')
                for count in range(len(track['events']) + 1):
                    phase = 'before' if count == track['before_count'] else 'after' if count == len(track['events']) else None
                    if phase:
                        for pool_id, expected in track['expected'][phase].items():
                            ranked = self.rank(FIXTURE['pools'][pool_id], datetime.fromisoformat(track['at']))
                            rows = {r['id']: r for r in ranked}
                            for saved in expected:
                                row = rows[saved['id']]
                                for field in ['comparison_score', 'profile_score', 'legacy_score']:
                                    self.assertAlmostEqual(row[field], saved[field], places=11)
                                self.assertAlmostEqual(row['score'], saved['profile_total_score'], places=11)
                    if count < len(track['events']):
                        event = track['events'][count]
                        self.choice(FIXTURE['decisions'][event['decision']], datetime.fromisoformat(event['at']))

    def test_fixed_text_unknown_never_raises_any_new_score_component(self):
        learn.learning_policy(self.db, 'adaptive-v1')
        track = next(t for t in FIXTURE['tracks'] if t['name'] == 'new_voice_tracks/paired')
        for event in track['events']:
            self.choice(FIXTURE['decisions'][event['decision']], datetime.fromisoformat(event['at']))
        for pool_id in track['expected']['after']:
            data = deepcopy(FIXTURE['pools'][pool_id])
            original = {r['id']: r for r in self.rank(data, datetime.fromisoformat(track['at']))}
            data.pop('edit_scope', None)
            for candidate in data['candidates']:
                for frame in candidate['grammar']['frames']:
                    if frame['kind'] == 'expression':
                        frame.update(form='unknown', voice='unknown', slots_complete=False)
                    else:
                        frame['relation'] = 'unknown'
                for unit in candidate['analysis']['units']:
                    unit['move'] = 'unknown'
            for row in self.rank(data, datetime.fromisoformat(track['at'])):
                for field in ['comparison_score', 'profile_score', 'score']:
                    self.assertLessEqual(row[field], original[row['id']][field] + 1e-12)

    def test_engine_info_and_cli_work_in_isolated_python(self):
        for args in [('engine-info',), ('learning-policy', self.db, 'ensure-adaptive')]:
            proc = subprocess.run([sys.executable, '-I', str(ROOT / 'library/learn.py'), *args],
                capture_output=True, text=True, cwd=self.root, timeout=20)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(proc.stdout)
            if args[0] == 'engine-info':
                self.assertEqual(result['database_schema'], 9)
                self.assertEqual(result['supported_scoring_versions'], [2, 3, 4, 5])
                self.assertIn('adaptive_preference_learning', result['capabilities'])
            else:
                self.assertEqual(result['policy'], 'adaptive-v3')


if __name__ == '__main__':
    unittest.main()
