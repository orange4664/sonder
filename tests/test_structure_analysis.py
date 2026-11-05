"""Grounded model annotations are soft evidence, not verified semantics."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
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
SPEC = importlib.util.spec_from_file_location('analysis_learn', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(learn)
PREFIX = 'syntax:analysis-v2:writing-model:'
NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


def candidate(key, pieces):
    """Explicit test annotations; these are fixtures, not model predictions."""
    text = '，'.join(piece[0] for piece in pieces) + '。'
    spans, cursor = [], 0
    for fragment, move in pieces:
        start = text.index(fragment, cursor)
        cursor = start + len(fragment)
        spans.append({'start': start, 'end': cursor, 'text': fragment,
                      'move': move, 'polarity': 'affirmative', 'certainty': 'asserted'})
    return {'id': key, 'label': 'Same label', 'text': text,
            'analysis': {'version': 1, 'source': 'writing-model',
                         'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'spans': spans}}


class StructureAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sonder-analysis-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = str(self.root / 'preference.db')
        self.file = self.root / 'choice.json'
        method, result = ('激光散射测定粒径', 'method'), ('均值为 24 nm[1]', 'result')
        self.items = [candidate('forward', [method, result]), candidate('reverse', [result, method])]
        # Isolate the new source from old cue/parser features for causal assertions.
        self.base = patch.object(learn, '_syntax_features', return_value=([], 'heuristic'))
        self.base.start()
        self.addCleanup(self.base.stop)
        self.clock = patch.object(learn, '_now_iso', return_value=NOW.isoformat())
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def submit(self, items=None, chosen='forward', event='round-1'):
        payload = {'candidates': self.items if items is None else items,
                   'chosen': chosen, 'author_owned': True, 'round_id': event}
        self.file.write_text(json.dumps(payload, ensure_ascii=False))
        return learn.choice_file(self.db, 'results', 'support', self.file)

    def snapshot(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return list(conn.iterdump())

    def test_annotation_changes_unseen_word_order_with_equal_words_and_labels(self):
        for i in range(3):
            self.submit(event=f'train-{i}')
        method, result = ('重复采样覆盖十个位置', 'method'), ('中位数为 7 μm[2]', 'result')
        probes = [candidate('reverse', [result, method]), candidate('forward', [method, result])]
        rows = learn.rank_candidates(self.db, 'results', 'support', probes)
        self.assertEqual(rows[0]['id'], 'forward')
        self.assertEqual(rows[0]['phrase_score'], rows[1]['phrase_score'])
        self.assertGreater(rows[0]['syntax_score'], rows[1]['syntax_score'])
        self.assertTrue(any('move_seq:method>result' in item['feature'] for item in rows[0]['matched_analysis_features']))
        self.assertEqual(rows[0]['analysis_status'], 'complete')
        for row in rows:
            self.assertEqual(row['analysis_source'], 'writing-model')
        empty = learn.rank_candidates(str(self.root / 'empty.db'), 'results', 'support', probes)
        self.assertEqual(empty[0]['score'], empty[1]['score'])
        isolated = learn.rank_candidates(self.db, 'discussion', 'support', probes)
        self.assertEqual(isolated[0]['score'], 0)
        for i in range(5):
            self.submit(probes, chosen='reverse', event=f'reverse-{i}')
        self.assertEqual(learn.rank_candidates(self.db, 'results', 'support', probes)[0]['id'], 'reverse')

    def test_author_text_receipt_and_analysis_hash_are_preserved(self):
        first = self.submit()
        before = self.snapshot()
        reordered = deepcopy(self.items)
        reordered[0]['analysis']['spans'].reverse()
        second = self.submit(reordered)
        self.assertEqual(second, {**first, 'replayed': True})
        self.assertEqual(self.snapshot(), before)
        changed = deepcopy(self.items)
        changed[0]['analysis']['spans'][0]['move'] = 'background'
        with self.assertRaisesRegex(ValueError, 'round_id'):
            self.submit(changed)
        self.assertEqual(self.snapshot(), before)
        with closing(sqlite3.connect(self.db)) as conn:
            text, rewards = conn.execute('SELECT text,rewards FROM exemplars').fetchone()
        self.assertEqual((text, rewards), (self.items[0]['text'], 1))

    def test_invalid_or_stale_analysis_fails_before_open_for_rank_and_choice(self):
        cases = []
        def altered(change):
            item = deepcopy(self.items[0]); change(item); cases.append(item)
        altered(lambda c: c.update(analysis=None))
        altered(lambda c: c['analysis'].update(version=True))
        altered(lambda c: c['analysis'].update(source='unverified-provider'))
        altered(lambda c: c['analysis'].update(confidence=0.99))
        altered(lambda c: c['analysis'].update(text_sha256='0' * 64))
        altered(lambda c: c.update(text=c['text'] + '尚不能排除偏差。'))
        for field, value in [('start', True), ('start', -1), ('end', 999), ('text', '旧句子'),
                             ('move', 'magic'), ('polarity', []), ('certainty', None), ('extra', 1)]:
            altered(lambda c, f=field, v=value: c['analysis']['spans'][0].update({f: v}))
        altered(lambda c: c['analysis']['spans'].append(deepcopy(c['analysis']['spans'][0])))
        altered(lambda c: c['analysis']['spans'].pop())  # unannotated result is not punctuation
        altered(lambda c: c['analysis'].update(spans=[{}] * 17))
        for item in cases:
            with self.subTest(item=item):
                with self.assertRaisesRegex(ValueError, 'analysis'):
                    learn.rank_candidates(self.db, 'results', 'support', [item])
                self.assertFalse(Path(self.db).exists())
                with self.assertRaisesRegex(ValueError, 'analysis'):
                    self.submit([item])
                self.assertFalse(Path(self.db).exists())

    def test_unknown_is_abstention_and_missing_annotation_is_not_rejection(self):
        self.submit()
        with closing(sqlite3.connect(self.db)) as conn:
            old = conn.execute('SELECT feature,weight FROM syntax_prefs').fetchall()
        missing = deepcopy(self.items)
        del missing[1]['analysis']
        result = self.submit(missing, chosen='reverse', event='missing')
        self.assertFalse(result['analysis_negative_feedback'])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT feature,weight FROM syntax_prefs').fetchall(), old)
        unknown = candidate('unknown', [('可见光照射显示器', 'unknown')])
        rows = learn.rank_candidates(self.db, 'results', 'support', [unknown])
        self.assertEqual(rows[0]['analysis_status'], 'abstained')
        self.assertEqual(rows[0]['syntax_score'], 0)
        # Unknown middle spans must never be collapsed into known adjacency/order.
        partial = candidate('partial', [('扫描得到信号', 'method'), ('关系仍不清楚', 'unknown'), ('幅值为 2 mV', 'result')])
        self.submit([partial], chosen='partial', event='partial')
        with closing(sqlite3.connect(self.db)) as conn:
            seq = conn.execute('SELECT picks FROM syntax_prefs WHERE feature=?', (PREFIX + 'move_seq:method>result',)).fetchone()
        self.assertEqual(seq[0], 1)

    def test_identical_text_cannot_carry_conflicting_analysis(self):
        twin = deepcopy(self.items[0]); twin['id'] = 'twin'
        twin['analysis']['spans'][0]['move'] = 'background'
        with self.assertRaisesRegex(ValueError, 'analysis'):
            self.submit([self.items[0], twin])
        self.assertFalse(Path(self.db).exists())

    def test_annotated_choice_does_not_reject_unobserved_legacy_academic_source(self):
        self.base.stop()
        legacy = [{'id': 'forward', 'label': 'A', 'text': '通过实验获得信号，结果表明样品稳定。'}]
        self.submit(legacy)
        with closing(sqlite3.connect(self.db)) as conn:
            old = conn.execute("SELECT * FROM syntax_prefs WHERE feature LIKE '%:academic:%'").fetchall()
        self.assertTrue(old)
        own = candidate('other', [('逐点扫描记录响应', 'method'), ('中位数为 7 μm[2]', 'result')])
        self.submit([own, *legacy], chosen='other', event='mixed-sources')
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT * FROM syntax_prefs WHERE feature LIKE '%:academic:%'").fetchall(), old)

    def test_explicit_analysis_replaces_surface_academic_but_off_disables_all(self):
        self.base.stop()
        unknown = candidate('unknown', [('因此样品失效', 'unknown')])
        result = self.submit([unknown], chosen='unknown')
        self.assertEqual(result['analysis_features'], 0)
        with closing(sqlite3.connect(self.db)) as conn:
            keys = [row[0] for row in conn.execute('SELECT feature FROM syntax_prefs')]
        self.assertFalse(any(':academic:' in key or key.startswith(PREFIX) for key in keys))
        learn.set_syntax_backend(self.db, 'off')
        rows = learn.rank_candidates(self.db, 'results', 'support', self.items)
        self.assertTrue(all(row['analysis_status'] == 'disabled' and row['syntax_score'] == 0 for row in rows))

    def test_polarity_certainty_and_unicode_ranges_are_not_flattened(self):
        item = candidate('forward', [('🧪未检出 $x=2$ 的峰[3]', 'result')])
        item['analysis']['spans'][0].update(polarity='negative', certainty='hedged')
        self.submit([item])
        rows = learn.rank_candidates(self.db, 'results', 'support', [item])
        keys = [entry['feature'] for entry in rows[0]['matched_analysis_features']]
        self.assertTrue(any('result/negative/hedged' in key for key in keys))
        # A UTF-16 start/end would mismatch the exact Python Unicode slice.
        stale = deepcopy(item); stale['analysis']['spans'][0]['start'] = 1
        with self.assertRaisesRegex(ValueError, 'analysis'):
            self.submit([stale], event='stale')

    def test_annotation_failure_rolls_back_phrase_exemplar_and_receipt(self):
        learn.list_exemplars(self.db)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("CREATE TRIGGER fail_analysis BEFORE INSERT ON syntax_prefs "
                         "WHEN NEW.feature LIKE 'syntax:analysis-v2:%' "
                         "BEGIN SELECT RAISE(ABORT, 'analysis failed'); END")
        before = self.snapshot()
        with self.assertRaises(sqlite3.DatabaseError):
            self.submit()
        self.assertEqual(self.snapshot(), before)

    def test_legacy_fingerprint_and_receipt_stay_compatible(self):
        legacy = [{k: v for k, v in c.items() if k != 'analysis'} for c in self.items]
        expected = {'version': 1, 'section': 'results', 'role': 'support',
                    'candidates': sorted(legacy, key=lambda c: c['id']),
                    'chosen': 'forward', 'author_owned': True}
        old_hash = hashlib.sha256(json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(learn._choice_payload_hash('results', 'support', legacy, 'forward', True), old_hash)
        # A real pre-analysis receipt has none of the new diagnostic fields.
        learn.list_exemplars(self.db)
        first = {'chosen': 'forward', 'label': 'Same label', 'features': 12,
                 'syntax_features': 5, 'syntax_backend': 'heuristic',
                 'syntax_model_version': None, 'author_exemplar': True,
                 'round_id': 'round-1', 'idempotent': True, 'replayed': False}
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('INSERT INTO choice_events VALUES (?,?,?,?)',
                         ('round-1', old_hash, json.dumps(first), NOW.isoformat()))
        before = self.snapshot()
        self.assertEqual(self.submit(legacy), {**first, 'replayed': True})
        self.assertEqual(self.snapshot(), before)

    def test_documented_annotation_example_runs_through_real_cli(self):
        doc = (ROOT / 'references/structure-analysis.md').read_text()
        code = doc.split('<!-- sonder-analysis-example -->', 1)[1].split('```python\n', 1)[1].split('```', 1)[0]
        built = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True, check=True)
        item = json.loads(built.stdout)
        self.file.write_text(json.dumps({'round_id': 'documented-example', 'candidates': [item],
                                         'chosen': item['id'], 'author_owned': False}, ensure_ascii=False))
        command = [sys.executable, '-I', str(ROOT / 'library/learn.py'), 'choice-file',
                   self.db, 'results', 'support', str(self.file)]
        first = json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)
        before = self.snapshot()
        replay = json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)
        self.assertGreater(first['analysis_features'], 0)
        self.assertEqual(replay, {**first, 'replayed': True})
        self.assertEqual(self.snapshot(), before)

    def test_annotation_weights_decay_with_existing_store(self):
        self.submit()
        first = learn.rank_candidates(self.db, 'results', 'support', self.items)[0]['syntax_score']
        with patch.object(learn, '_now_iso', return_value=(NOW + timedelta(days=30)).isoformat()):
            later = learn.rank_candidates(self.db, 'results', 'support', self.items)[0]['syntax_score']
        self.assertAlmostEqual(later, first / 2, delta=1)

    def split_variants(self):
        text = '我们拟合光谱并提取峰位，谐振频率为 3.2 MHz[1]。'
        variants = []
        for parts in ([('我们拟合光谱并提取峰位', 'method'), ('谐振频率为 3.2 MHz[1]', 'result')],
                      [('我们拟合光谱', 'method'), ('并提取峰位', 'method'), ('谐振频率为 3.2 MHz[1]', 'result')]):
            item = candidate('forward', parts)
            # Both representations bind to exactly the same underlying sentence.
            item['text'] = text
            item['analysis']['text_sha256'] = hashlib.sha256(text.encode()).hexdigest()
            cursor = 0
            for span in item['analysis']['spans']:
                span['start'] = text.index(span['text'], cursor)
                span['end'] = span['start'] + len(span['text'])
                cursor = span['end']
            variants.append(item)
        return variants

    def test_same_signature_splitting_keeps_scores_and_feedback_identical(self):
        coarse, fine = self.split_variants()
        self.submit()
        scores = [learn.rank_candidates(self.db, 'results', 'support', [item])[0] for item in (coarse, fine)]
        self.assertEqual(scores[0]['syntax_score'], scores[1]['syntax_score'])
        self.assertGreater(scores[0]['syntax_score'], 0)
        self.assertEqual(scores[0]['matched_analysis_features'], scores[1]['matched_analysis_features'])
        # Feedback itself, including negative evidence against the reverse option,
        # must also be invariant; mere rank normalization is insufficient.
        snapshots = []
        for i, item in enumerate((coarse, fine)):
            db = str(self.root / f'split-{i}.db')
            self.file.write_text(json.dumps({'round_id': 'same-trial', 'candidates': [item, self.items[1]],
                                             'chosen': 'forward', 'author_owned': True}, ensure_ascii=False))
            learn.choice_file(db, 'results', 'support', self.file)
            with closing(sqlite3.connect(db)) as conn:
                snapshots.append(conn.execute('SELECT * FROM syntax_prefs ORDER BY feature').fetchall())
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertTrue(any('move_seq:method>result' in row[1] for row in snapshots[0]))

    def test_changed_polarity_certainty_and_unknown_prevent_merging(self):
        coarse, fine = self.split_variants()
        baseline = learn._analysis_features(coarse['analysis'])
        for field, value in [('polarity', 'negative'), ('certainty', 'hedged'),
                             ('move', 'unknown'), ('polarity', 'unknown'), ('certainty', 'unknown')]:
            changed = deepcopy(fine)
            changed['analysis']['spans'][1][field] = value
            with self.subTest(field=field, value=value):
                self.assertNotEqual(learn._analysis_features(changed['analysis']), baseline)
                if field == 'move':
                    self.assertFalse(any('method>result' in feature for feature in learn._analysis_features(changed['analysis'])))
        # Evidence is immutable: even equivalent extraction is not permission to
        # rewrite committed span boundaries under the same decision ID.
        self.submit([coarse])
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'round_id'):
            self.submit([fine])
        self.assertEqual(self.snapshot(), before)

    def test_old_analysis_weights_are_preserved_but_not_reinterpreted(self):
        old_prefix = 'syntax:analysis-v1:writing-model:'
        learn.list_exemplars(self.db)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('INSERT INTO syntax_prefs VALUES (?,?,?,?,?)',
                         ('results::support', old_prefix + 'move_seq:method>result', 999999, 7, NOW.isoformat()))
        rows = learn.rank_candidates(self.db, 'results', 'support', self.items)
        self.assertTrue(all(row['syntax_score'] == 0 for row in rows))
        self.submit()
        with closing(sqlite3.connect(self.db)) as conn:
            old = conn.execute('SELECT weight,picks FROM syntax_prefs WHERE feature=?',
                               (old_prefix + 'move_seq:method>result',)).fetchone()
        self.assertEqual(old, (999999, 7))
        self.assertEqual(learn.rank_candidates(self.db, 'results', 'support', self.items)[0]['analysis_feature_version'], 'v2')


if __name__ == '__main__':
    unittest.main()
