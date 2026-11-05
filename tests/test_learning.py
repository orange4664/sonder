"""Behavior regressions for fresh, upgraded, and long-lived learning stores."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import importlib.util
import itertools
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LEARN = ROOT / "library/learn.py"
spec = importlib.util.spec_from_file_location("learn", LEARN)
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)
NOW = datetime(2026, 10, 22, 4, 0, tzinfo=timezone.utc)
CHINESE = "我们测量了三个站点的迁移率，没有观察到位错累积。"


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sonder-regression-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = str(self.root / "preference.db")

    def cli(self, *args, success=True):
        p = subprocess.run([sys.executable, "-I", str(LEARN), *map(str, args)],
                           cwd=self.root, capture_output=True, text=True, timeout=20)
        if success:
            self.assertEqual(p.returncode, 0, p.stderr)
        else:
            self.assertNotEqual(p.returncode, 0, p.stdout)
        return p

    def age(self, table, days):
        self.assertIn(table, ("prefs", "phrase_prefs", "syntax_prefs", "exemplars"))
        last = (NOW - timedelta(days=days)).isoformat()
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute(f"UPDATE {table} SET last_at=?", (last,))

    def raw(self, query, args=()):
        with closing(sqlite3.connect(self.db)) as c:
            return c.execute(query, args).fetchall()

    def legacy(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.executescript((ROOT / "tests/fixtures/legacy-schema.sql").read_text())
            # Chronological order differs from lexical order for these offsets.
            c.executemany(
                "INSERT INTO exemplars(id,kind,text,ngram,created,last_at,rewards) "
                "VALUES(?,?,?,?,?,?,?)", [
                    (7, "author", CHINESE, "{}", "2026-01-01 00:00:00",
                     "2026-02-01T00:00:00+00:00", 2),
                    (9, "author", CHINESE, "{}", "2026-01-01T01:00:00+08:00",
                     "2026-02-01T01:00:00+08:00", 3),
                    (11, "kept", CHINESE, "{}", "2026-01-03T00:00:00+00:00",
                     "2026-02-02T00:00:00+00:00", 1),
                ])
            c.execute("INSERT INTO prefs(context,opt,weight,picks) VALUES(?,?,?,?)",
                      ("intro::topic", "Medium", 5000, 5))

    def test_legacy_upgrade_preserves_history_and_rebuilds_chinese_index(self):
        self.legacy()
        rows = learn.list_exemplars(self.db, "author")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["id"], rows[0]["rewards"]), (7, 5))
        self.assertEqual(learn.nearest(self.db, CHINESE, 1)[0]["sim"], 1.0)
        dates = self.raw("SELECT created,last_at FROM exemplars WHERE id=7")[0]
        self.assertEqual(datetime.fromisoformat(dates[0]),
                         datetime(2025, 12, 31, 17, tzinfo=timezone.utc))
        self.assertEqual(datetime.fromisoformat(dates[1]),
                         datetime(2026, 2, 1, tzinfo=timezone.utc))
        self.assertEqual(self.raw("SELECT weight,picks FROM prefs"), [(5000, 5)])
        self.assertEqual(learn.list_exemplars(self.db, "author")[0]["rewards"], 5)
        self.assertEqual(len(learn.list_exemplars(self.db, "kept")), 1)
        learn.store(self.db, "author", CHINESE)
        rows = learn.list_exemplars(self.db, "author")
        self.assertEqual((len(rows), rows[0]["rewards"]), (1, 6))

    def test_migration_rolls_back_on_error(self):
        self.legacy()
        before = self.raw("SELECT * FROM exemplars ORDER BY id")
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("CREATE TRIGGER fail_migration BEFORE UPDATE OF ngram ON exemplars "
                      "BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            learn.list_exemplars(self.db)
        self.assertEqual(self.raw("SELECT * FROM exemplars ORDER BY id"), before)
        self.assertEqual(self.raw("PRAGMA user_version"), [(0,)])
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("DROP TRIGGER fail_migration")
        self.assertEqual(len(learn.list_exemplars(self.db, "author")), 1)

    def test_unversioned_unique_table_also_rebuilds_cached_index(self):
        self.legacy()
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("DELETE FROM exemplars WHERE id=9")
            c.execute("CREATE UNIQUE INDEX old_unique ON exemplars(kind,text)")
        self.assertEqual(learn.nearest(self.db, CHINESE, 1)[0]["sim"], 1.0)

    def test_newer_database_is_rejected_without_changes(self):
        self.legacy()
        before = self.raw("SELECT * FROM exemplars ORDER BY id")
        with closing(sqlite3.connect(self.db)) as c:
            c.execute(f"PRAGMA user_version = {learn.SCHEMA_VERSION + 1}")
        self.cli("list", self.db, success=False)
        self.assertEqual(self.raw("SELECT * FROM exemplars ORDER BY id"), before)
        self.assertEqual(self.raw("PRAGMA user_version"), [(learn.SCHEMA_VERSION + 1,)])

    def test_aged_rejection_only_decreases_effective_weight(self):
        with patch.object(learn, "_now_iso", return_value=NOW.isoformat()):
            learn.record(self.db, "intro", "topic", "Medium", 100000)
            self.age("prefs", 60)
            learn.signal(self.db, "intro", "topic", "Medium", 500)
            self.assertEqual(self.raw("SELECT weight,picks FROM prefs"), [(24500, 1)])

    def test_aged_positive_feedback_does_not_resurrect_raw_weight(self):
        with patch.object(learn, "_now_iso", return_value=NOW.isoformat()):
            learn.record(self.db, "intro", "topic", "Medium", 100000)
            self.age("prefs", 60)
            learn.record(self.db, "intro", "topic", "Medium", 1000)
            self.assertEqual(self.raw("SELECT weight,picks FROM prefs"), [(26000, 2)])

    def test_rejection_floor_and_never_chosen_option(self):
        learn.signal(self.db, "intro", "topic", "Bold")
        self.assertEqual(self.raw("SELECT * FROM prefs"), [])
        learn.record(self.db, "intro", "topic", "Light", 2000)
        learn.signal(self.db, "intro", "topic", "Light", 100000)
        self.assertEqual(self.raw("SELECT weight,picks FROM prefs"), [(1, 1)])
        self.age("prefs", 400)
        before = self.raw("SELECT weight,last_at FROM prefs")
        learn.signal(self.db, "intro", "topic", "Light")
        self.assertEqual(self.raw("SELECT weight,last_at FROM prefs"), before)

    def test_weight_ceiling_on_insert_update_and_global(self):
        for _ in range(2):
            learn.record(self.db, "intro", "topic", "Bold", learn.CEILING)
            learn.global_pref(self.db, "Medium", learn.CEILING)
        self.assertEqual(self.raw("SELECT weight FROM prefs ORDER BY context"),
                         [(learn.CEILING,), (learn.CEILING,)])

    def test_cli_default_weight_context_and_prune(self):
        self.cli("record", self.db, "methods", "detail", "Light")
        self.cli("prune", self.db)
        bias = json.loads(self.cli("bias", self.db, "methods", "detail", "Light", "Bold").stdout)
        self.assertGreaterEqual(bias["Light"]["weight"], 990)
        self.assertEqual(bias["Light"]["picks"], 1)
        self.assertEqual(bias["Bold"], {"weight": 0, "picks": 0})
        other = json.loads(self.cli("bias", self.db, "intro", "topic", "Light").stdout)
        self.assertEqual(other["Light"]["weight"], 0)

    def test_candidate_text_learning_survives_label_rename_and_context_separation(self):
        candidate_path = self.root / "candidates.json"
        candidates = [
            {"id": "accepted", "label": "Light", "text": "我们测量了稳定流量。"},
            {"id": "rejected", "label": "Bold", "text": "笔者观察到稳定流量。"},
        ]
        for _ in range(3):
            candidate_path.write_text(json.dumps({
                "candidates": candidates,
                "chosen": "accepted",
                "author_owned": True,
            }, ensure_ascii=False))
            self.cli("choice-file", self.db, "results", "topic", candidate_path)

        renamed = [
            {"id": "rejected", "label": "Renamed A", "text": candidates[1]["text"]},
            {"id": "accepted", "label": "Renamed B", "text": candidates[0]["text"]},
        ]
        candidate_path.write_text(json.dumps({"candidates": renamed}, ensure_ascii=False))
        ranked = json.loads(self.cli(
            "rank-file", self.db, "results", "topic", candidate_path).stdout)
        self.assertEqual(ranked[0]["id"], "accepted")
        self.assertGreater(ranked[0]["phrase_score"], ranked[1]["phrase_score"])
        self.assertEqual(ranked[0]["label_weight"], 0)
        self.assertEqual(self.raw("SELECT picks FROM prefs WHERE opt='Light'"), [(3,)])
        self.assertGreater(self.raw("SELECT COUNT(*) FROM phrase_prefs")[0][0], 0)
        self.assertAlmostEqual(learn.nearest(self.db, candidates[0]["text"], 1)[0]["sim"], 1.0)

        other_context = json.loads(self.cli(
            "rank-file", self.db, "methods", "topic", candidate_path).stdout)
        self.assertTrue(all(item["phrase_score"] == 0 for item in other_context))

    def test_author_sentence_teaches_structure_and_generalizes_to_new_words(self):
        candidate_path = self.root / "syntax-candidates.json"
        chosen = "我们通过实验测量稳定流量，再比较不同条件下的变化。"
        reversed_order = "我们先比较不同条件下的变化，再通过实验测量稳定流量。"
        for _ in range(3):
            candidate_path.write_text(json.dumps({
                "candidates": [
                    {"id": "chosen", "label": "Light", "text": chosen},
                    {"id": "reversed", "label": "Bold", "text": reversed_order},
                ],
                "chosen": "chosen",
                "author_owned": True,
            }, ensure_ascii=False))
            self.cli("choice-file", self.db, "results", "support", candidate_path)

        learned = self.raw(
            "SELECT COUNT(*), MAX(picks) FROM syntax_prefs WHERE context=?",
            ("results::support",),
        )[0]
        self.assertGreater(learned[0], 0)
        self.assertEqual(learned[1], 3)
        self.assertEqual(learn.list_exemplars(self.db, "author")[0]["text"], chosen)

        new_words = [
            {"id": "same", "label": "Renamed-A", "text":
             "我们通过仿真测量扩散系数，再比较不同温度下的变化。"},
            {"id": "different", "label": "Renamed-B", "text":
             "我们先比较不同温度下的变化，再通过仿真测量扩散系数。"},
        ]
        candidate_path.write_text(json.dumps({"candidates": new_words}, ensure_ascii=False))
        ranked = json.loads(self.cli(
            "rank-file", self.db, "results", "support", candidate_path).stdout)
        self.assertEqual(ranked[0]["id"], "same")
        self.assertGreater(ranked[0]["syntax_score"], ranked[1]["syntax_score"])
        self.assertTrue(ranked[0]["matched_syntax_features"])
        self.assertTrue(all(item["label_weight"] == 0 for item in ranked))

    def test_round_feedback_ignores_candidate_order_and_duplicate_alternatives(self):
        chosen = {"id": "chosen", "label": "Own", "text":
                  "我们通过实验测量流量，结果表明样品稳定。"}
        other = {"id": "other", "label": "B", "text":
                 "结果表明样品稳定，我们通过实验测量流量。"}
        contrast = {"id": "contrast", "label": "C", "text":
                    "然而样品的流量尚未稳定。"}
        rounds = list(itertools.permutations([chosen, other, contrast]))
        rounds.append([chosen, other, contrast] + [
            {**other, "id": f"duplicate-{i}"} for i in range(6)])
        rounds.append([chosen, other, contrast, {**chosen, "id": "same-text"}])
        snapshots = []
        with patch.object(learn, "_now_iso", return_value=NOW.isoformat()):
            for index, candidates in enumerate(rounds):
                path = self.root / f"round-{index}.json"
                db = str(self.root / f"round-{index}" / "preference.db")
                path.write_text(json.dumps({"candidates": candidates,
                    "chosen": "chosen", "author_owned": True}, ensure_ascii=False))
                for _ in range(3):
                    learn.choice_file(db, "results", "support", path)
                with closing(sqlite3.connect(db)) as conn:
                    snapshots.append({table: conn.execute(
                        f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
                        for table in ("prefs", "phrase_prefs", "syntax_prefs", "exemplars")})
                    self.assertEqual(conn.execute(
                        "SELECT weight,picks FROM phrase_prefs WHERE feature='cjk2:我们'"
                    ).fetchone(), (3000, 3))
        for snapshot in snapshots[1:]:
            self.assertEqual(snapshot, snapshots[0])

    def test_round_rejects_exclusive_features_once_from_decayed_weight(self):
        rejected = {"id": "old", "label": "Old", "text": "然而样品尚未稳定。"}
        chosen = {"id": "new", "label": "New", "text": "我们通过实验测量流量。"}
        path = self.root / "correction.json"
        path.write_text(json.dumps({"candidates": [rejected], "chosen": "old"}))
        with patch.object(learn, "_now_iso", return_value=NOW.isoformat()):
            for _ in range(4):
                learn.choice_file(self.db, "results", "support", path)
            self.age("phrase_prefs", 30)
            self.age("syntax_prefs", 30)
            path.write_text(json.dumps({"candidates": [chosen, rejected,
                {**rejected, "id": "duplicate"}], "chosen": "new"}))
            learn.choice_file(self.db, "results", "support", path)
        self.assertEqual(self.raw(
            "SELECT weight,picks FROM phrase_prefs WHERE feature='cjk2:然而'"),
            [(1750, 4)])
        feature = learn._syntax_feature_name("academic_move_seq", "contrast>limitation", "academic")
        self.assertEqual(self.raw(
            "SELECT weight,picks FROM syntax_prefs WHERE feature=?", (feature,)),
            [(1750, 4)])

    def test_academic_argument_layer_learns_move_order_and_auditable_cues(self):
        candidate_path = self.root / "academic-candidates.json"
        chosen = "我们通过实验测量稳定流量，结果表明系统保持稳定。"
        reversed_order = "结果表明系统保持稳定，我们通过实验测量稳定流量。"
        for _ in range(3):
            candidate_path.write_text(json.dumps({
                "candidates": [
                    {"id": "chosen", "label": "A", "text": chosen},
                    {"id": "reversed", "label": "B", "text": reversed_order},
                ],
                "chosen": "chosen",
                "author_owned": True,
            }, ensure_ascii=False))
            self.cli("choice-file", self.db, "results", "argument", candidate_path)

        positive_features = learn._heuristic_syntax_features(chosen)
        self.assertTrue(any("academic_cue:method@" in feature
                            for feature in positive_features))
        self.assertTrue(any("academic_cue:result@" in feature
                            for feature in positive_features))
        self.assertTrue(any("academic_move_seq:method>result" in feature
                            for feature in positive_features))
        self.assertTrue(any("academic_slot_template" in feature
                            for feature in positive_features))
        self.assertTrue(any("academic_punct_shape" in feature
                            for feature in positive_features))

        new_words = [
            {"id": "same", "label": "renamed-a",
             "text": "我们通过仿真测量扩散系数，结果表明系统保持稳定。"},
            {"id": "different", "label": "renamed-b",
             "text": "结果表明系统保持稳定，我们通过仿真测量扩散系数。"},
        ]
        candidate_path.write_text(json.dumps({"candidates": new_words},
                                             ensure_ascii=False))
        ranked = json.loads(self.cli(
            "rank-file", self.db, "results", "argument", candidate_path).stdout)
        self.assertEqual(ranked[0]["id"], "same")
        self.assertGreater(ranked[0]["syntax_score"], ranked[1]["syntax_score"])
        self.assertTrue(any("academic_move_seq" in item["feature"]
                            for item in ranked[0]["matched_syntax_features"]))

        hedge_limit = learn._heuristic_syntax_features(
            "结果可能提示系统尚未稳定，仍需进一步验证。")
        self.assertTrue(any("academic_cue:hedge@" in feature
                            for feature in hedge_limit))
        self.assertTrue(any("academic_cue:limitation@" in feature
                            for feature in hedge_limit))

    def test_academic_layer_is_parser_independent_and_off_is_silent(self):
        text = "我们通过实验测量样品，结果显示系统稳定。"
        heuristic = learn._heuristic_syntax_features(text)
        self.assertTrue(any("academic_move_seq:method>result" in feature
                            for feature in heuristic))
        learn.set_syntax_backend(self.db, "off")
        self.assertEqual(learn._syntax_features(text, self.db), ([], "off"))

    def test_academic_cue_boundaries_preserve_real_cues_and_exclude_nouns(self):
        cases = json.loads((ROOT / "tests/fixtures/academic-cue-boundaries.json").read_text())
        for case in cases:
            with self.subTest(text=case["text"]):
                features = learn._heuristic_syntax_features(case["text"])
                sequences = [f.split("academic_move_seq:", 1)[1].split(">")
                             for f in features if "academic_move_seq:" in f]
                self.assertEqual(sequences, [case["moves"]] if case["moves"] else [])
                for move in case["forbidden"]:
                    self.assertFalse(any(f":cue_pos:{move}@" in f
                                         or f":academic_cue:{move}@" in f for f in features))
        for text, marker in (("样品通过率为 90%。", "通过"),
                             ("测试显示器的刷新率。", "显示")):
            template = learn._heuristic_slot_template(text)
            self.assertNotIn(marker, template.split())

    def test_technical_nouns_do_not_teach_phantom_academic_preferences(self):
        path = self.root / "technical-noun.json"
        for text in ("我们采用可见光测量薄膜透射率。", "测试显示器的刷新率。", "样品通过率为 90%。"):
            path.write_text(json.dumps({"candidates": [{"id": "a", "text": text}],
                                       "chosen": "a", "author_owned": True}))
            learn.choice_file(self.db, "results", "support", path)
        candidates = [{"id": "q", "label": "Query", "text": "可见样品尚未达到热平衡。"}]
        matched = learn.rank_candidates(self.db, "results", "support", candidates)[0]["matched_syntax_features"]
        self.assertFalse(any("academic_cue:conclusion@" in f["feature"] for f in matched))
        self.assertEqual(self.raw(
            "SELECT COUNT(*) FROM syntax_prefs WHERE feature LIKE '%academic_move_seq:result'"), [(0,)])
        self.assertEqual(len(learn.list_exemplars(self.db, "author")), 3)

    def test_new_structure_namespace_preserves_but_does_not_reuse_v2_weights(self):
        learn.list_exemplars(self.db)
        old_feature = "syntax:v2:academic:academic_cue:conclusion@only"
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO syntax_prefs VALUES (?,?,?,?,?)",
                         ("results::support", old_feature, 100000, 100, NOW.isoformat()))
        with patch.object(learn, "_now_iso", return_value=NOW.isoformat()):
            result = learn.rank_candidates(self.db, "results", "support", [
                {"id": "a", "label": "A", "text": "可见样品稳定。"}])[0]
        self.assertEqual(result["syntax_score"], 0)
        self.assertEqual(self.raw("SELECT feature,weight,picks FROM syntax_prefs"),
                         [(old_feature, 100000, 100)])

    def test_syntax_backend_falls_back_without_network_or_model(self):
        self.assertEqual(learn.get_syntax_backend(self.db), "heuristic")
        learn.set_syntax_backend(self.db, "stanza", str(self.root / "missing-model"))
        features, backend = learn._syntax_features("结果表明样品稳定。", self.db)
        self.assertTrue(features)
        self.assertEqual(backend, "heuristic-fallback")
        empty_model = self.root / "empty-model"
        empty_model.mkdir()
        learn.set_syntax_backend(self.db, "stanza", empty_model, "missing-local-model")
        features, backend = learn._syntax_features("结果表明样品稳定。", self.db)
        self.assertTrue(features)
        self.assertEqual(backend, "heuristic-fallback")
        learn.set_syntax_backend(self.db, "off")
        self.assertEqual(learn._syntax_features("结果表明样品稳定。", self.db), ([], "off"))

    def test_stanza_loader_supplies_dependency_prerequisites_offline(self):
        model_dir = self.root / "dependency-model"
        model_dir.mkdir()
        (model_dir / 'weights.bin').write_bytes(b'fixture')
        config = {f'{p}_model_path': str(model_dir / 'weights.bin')
                  for p in ('tokenize','pos','lemma','depparse')}
        calls = []
        document = self.parser_document([[
            ("团队", "NOUN", 2, "nsubj"),
            ("测量", "VERB", 0, "root"),
            ("样品", "NOUN", 2, "obj"),
        ]])

        class DependencyCheckedPipeline:
            def __init__(self, **kwargs):
                calls.append(kwargs)
                self.config = config
                # Stanza's actual DepparseProcessor requires these annotations.
                # Unlike the output-only parser fixture, reject invalid wiring
                # before simulating successful inference.
                provided = set()
                requirements = {"tokenize": set(), "pos": {"tokenize"},
                                "lemma": {"tokenize"},
                                "depparse": {"tokenize", "pos", "lemma"}}
                for processor in kwargs["processors"].split(","):
                    missing = requirements[processor] - provided
                    if missing:
                        raise ValueError(f"missing prerequisites: {sorted(missing)}")
                    provided.add(processor)

            def __call__(self, text):
                return document

        fake_stanza = types.SimpleNamespace(
            Pipeline=DependencyCheckedPipeline,
            DownloadMethod=types.SimpleNamespace(NONE="no-download"),
            __version__='fixture-v1',
        )
        with patch.dict(sys.modules, {"stanza": fake_stanza}), patch.object(learn, '_stanza_model_config', return_value=config):
            learn.set_syntax_backend(self.db, "stanza", model_dir, "fixture")
            diagnostics = {}
            features, backend = learn._syntax_features("团队测量样品", self.db, diagnostics)
        self.assertEqual(backend, "stanza", diagnostics.get("fallback_reason"))
        self.assertTrue(any(":dep_shape:" in feature for feature in features))
        self.assertIsNone(diagnostics["fallback_reason"])
        self.assertEqual(calls[0]["download_method"], "no-download")
        self.assertEqual(calls[0]["dir"], str(model_dir))
        self.assertEqual(calls[0]["lang"], "zh-hans")
        self.assertEqual(calls[0]["package"], "default_fast")
        self.assertNotEqual(calls[0].get("check_requirements"), False)
        self.assertFalse(calls[0].get("pretagged"))
        self.assertFalse(calls[0].get("depparse_pretagged"))

    def test_local_parser_adapter_normalizes_pos_dependency_and_slots(self):
        model_dir = self.root / "local-model"
        model_dir.mkdir()
        (model_dir / 'weights.bin').write_bytes(b'fixture')
        config = {f'{p}_model_path': str(model_dir / 'weights.bin')
                  for p in ('tokenize','pos','lemma','depparse')}

        class FakeWord:
            def __init__(self, token_id, text, upos, head, deprel):
                self.id = token_id
                self.text = text
                self.upos = upos
                self.head = head
                self.deprel = deprel

        class FakeToken:
            def __init__(self, word):
                self.text = word.text
                self.words = [word]

        class FakeSentence:
            def __init__(self, words):
                self.tokens = [FakeToken(word) for word in words]

        class FakeDocument:
            def __init__(self):
                self.sentences = [FakeSentence([
                    FakeWord(1, "我们", "PRON", 3, "nsubj"),
                    FakeWord(2, "通过", "ADP", 3, "case"),
                    FakeWord(3, "测量", "VERB", 0, "root"),
                    FakeWord(4, "样品", "NOUN", 3, "obj"),
                    FakeWord(5, "。", "PUNCT", 3, "punct"),
                ])]

        class FakePipeline:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.config = config

            def __call__(self, text):
                self.text = text
                return FakeDocument()

        fake_stanza = types.SimpleNamespace(
            Pipeline=FakePipeline,
            DownloadMethod=types.SimpleNamespace(NONE="no-download"),
            __version__='fixture-v1',
        )
        with patch.dict(sys.modules, {"stanza": fake_stanza}), patch.object(learn, '_stanza_model_config', return_value=config):
            learn.set_syntax_backend(self.db, "stanza", model_dir, "fake-1")
            diagnostics = {}
            features, backend = learn._syntax_features("我们通过测量样品。", self.db, diagnostics)
        self.assertEqual(backend, "stanza")
        self.assertTrue(any("pos_seq" in feature for feature in features))
        self.assertTrue(any("dep_shape" in feature for feature in features))
        self.assertTrue(any("slot_template" in feature for feature in features))
        self.assertTrue(any("academic_cue:method@" in feature for feature in features))
        self.assertTrue(any("stanza-id1-" in feature for feature in features))
        self.assertEqual(diagnostics['model_version'], 'fake-1')

    def parser_document(self, sentences):
        return types.SimpleNamespace(sentences=[types.SimpleNamespace(tokens=[
            types.SimpleNamespace(text=text, words=[types.SimpleNamespace(
                id=index + 1, text=text, upos=upos, head=head, deprel=dep)])
            for index, (text, upos, head, dep) in enumerate(words)
        ]) for words in sentences])

    def identified_parser(self, document):
        parser = lambda text: document
        parser._sonder_model_fingerprint = 'a' * 64
        return parser

    def test_parser_does_not_treat_fragments_of_technical_nouns_as_cues(self):
        document = self.parser_document([[
            ("通过", "ADP", 2, "case"), ("率", "NOUN", 3, "nsubj"),
            ("升高", "VERB", 0, "root"), ("，", "PUNCT", 3, "punct"),
            ("显示", "VERB", 3, "conj"), ("器", "NOUN", 5, "obj"),
            ("工作正常", "VERB", 5, "conj"), ("。", "PUNCT", 3, "punct"),
        ]])
        learn.set_syntax_backend(self.db, "stanza")
        with patch.object(learn, "_stanza_pipeline", return_value=self.identified_parser(document)):
            features, backend = learn._syntax_features("通过率升高，显示器工作正常。", self.db)
        self.assertEqual(backend, "stanza")
        self.assertFalse(any(":cue_pos:" in f or ":academic:" in f for f in features))
        template = next(f for f in features if ":slot_template:" in f)
        self.assertNotIn("通过", template)
        self.assertNotIn("显示", template)

    def test_parser_preserves_sentence_order_and_scopes_dependency_ids(self):
        document = self.parser_document([
            [("我们", "PRON", 2, "nsubj"), ("测量", "VERB", 0, "root"),
             ("。", "PUNCT", 2, "punct")],
            [("样品", "NOUN", 2, "nsubj"), ("稳定", "ADJ", 0, "root"),
             ("。", "PUNCT", 2, "punct")],
        ])
        with patch.object(learn, "_stanza_pipeline", return_value=self.identified_parser(document)):
            features = learn._stanza_syntax_features("我们测量。样品稳定。", self.db)
        self.assertTrue(any(f.endswith(":pos_seq:PRON,V,PUNCT,N,ADJ,PUNCT") for f in features))
        self.assertTrue(any(f.endswith(":dep_shape:nsubj:V>PRON=1") for f in features))
        self.assertTrue(any(f.endswith(":dep_shape:nsubj:ADJ>N=1") for f in features))
        self.assertFalse(any(":dep_shape:nsubj:ADJ>PRON" in f for f in features))

    def test_syntax_schema_is_added_and_pruned_with_decay_contract(self):
        self.legacy()
        learn.list_exemplars(self.db)
        self.assertEqual(self.raw("PRAGMA user_version"), [(learn.SCHEMA_VERSION,)])
        self.assertEqual(self.raw(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='syntax_prefs'"),
            [(1,)])
        candidate_path = self.root / "syntax-prune.json"
        candidate_path.write_text(json.dumps({
            "candidates": [
                {"id": "a", "label": "A", "text": "结果表明样品稳定。"},
                {"id": "b", "label": "B", "text": "但是样品出现变化。"},
            ],
            "chosen": "a",
            "author_owned": True,
        }, ensure_ascii=False))
        with patch.object(learn, "_now_iso", return_value=NOW.isoformat()):
            learn.choice_file(self.db, "results", "conclusion", str(candidate_path))
        self.assertGreater(self.raw("SELECT COUNT(*) FROM syntax_prefs")[0][0], 0)
        self.age("syntax_prefs", 400)
        with patch.object(learn, "_now_iso", return_value=NOW.isoformat()):
            learn.prune(self.db)
        self.assertEqual(self.raw("SELECT COUNT(*) FROM syntax_prefs"), [(0,)])

    def test_v2_phrase_database_migrates_without_losing_phrase_rows(self):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.executescript("""
                CREATE TABLE prefs (
                    context TEXT NOT NULL, opt TEXT NOT NULL,
                    weight INTEGER NOT NULL DEFAULT 0,
                    picks INTEGER NOT NULL DEFAULT 0,
                    last_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (context, opt)
                );
                CREATE TABLE seen (
                    context TEXT NOT NULL, opt TEXT NOT NULL,
                    at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (context, opt)
                );
                CREATE TABLE exemplars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL, text TEXT NOT NULL, ngram TEXT NOT NULL,
                    created TEXT NOT NULL, last_at TEXT NOT NULL,
                    rewards INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE phrase_prefs (
                    context TEXT NOT NULL, feature TEXT NOT NULL,
                    weight INTEGER NOT NULL DEFAULT 0,
                    picks INTEGER NOT NULL DEFAULT 0,
                    last_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (context, feature)
                );
            """)
            connection.execute(
                "INSERT INTO phrase_prefs(context,feature,weight,picks) VALUES(?,?,?,?)",
                ("results::support", "cjk2:我们", 1800, 2),
            )
            connection.execute("PRAGMA user_version = 2")
        learn.list_exemplars(self.db)
        self.assertEqual(self.raw("PRAGMA user_version"), [(learn.SCHEMA_VERSION,)])
        self.assertEqual(self.raw(
            "SELECT context,feature,weight,picks FROM phrase_prefs"),
            [("results::support", "cjk2:我们", 1800, 2)],
        )
        self.assertEqual(self.raw(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='syntax_prefs'"),
            [(1,)])

    def test_syntax_feedback_rolls_back_with_phrase_feedback(self):
        candidate_path = self.root / "syntax-rollback.json"
        candidate_path.write_text(json.dumps({
            "candidates": [
                {"id": "a", "label": "A", "text": "结果表明样品稳定。"},
                {"id": "b", "label": "B", "text": "但是样品出现变化。"},
            ],
            "chosen": "a",
        }, ensure_ascii=False))
        self.cli("reshow", self.db)
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER fail_syntax BEFORE INSERT ON syntax_prefs "
                "BEGIN SELECT RAISE(ABORT, 'simulated syntax failure'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            learn.choice_file(self.db, "results", "conclusion", str(candidate_path))
        self.assertEqual(self.raw("SELECT COUNT(*) FROM phrase_prefs"), [(0,)])
        self.assertEqual(self.raw("SELECT COUNT(*) FROM syntax_prefs"), [(0,)])

    def test_phrase_schema_is_added_when_opening_legacy_database(self):
        self.legacy()
        self.assertEqual(self.raw("PRAGMA user_version"), [(0,)])
        learn.list_exemplars(self.db)
        self.assertEqual(self.raw("PRAGMA user_version"), [(learn.SCHEMA_VERSION,)])
        self.assertEqual(self.raw(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='phrase_prefs'"),
            [(1,)])

    def test_phrase_weights_decay_and_prune_with_the_same_contract(self):
        candidate_path = self.root / "phrase-candidates.json"
        candidate_path.write_text(json.dumps({
            "candidates": [
                {"id": "a", "label": "Light", "text": "我们测量流量。"},
                {"id": "b", "label": "Bold", "text": "笔者观察流量。"},
            ],
            "chosen": "a",
        }, ensure_ascii=False))
        with patch.object(learn, "_now_iso", return_value=NOW.isoformat()):
            learn.choice_file(self.db, "results", "topic", str(candidate_path))
        self.age("phrase_prefs", 400)
        with patch.object(learn, "_now_iso", return_value=NOW.isoformat()):
            learn.prune(self.db)
        self.assertEqual(self.raw("SELECT COUNT(*) FROM phrase_prefs"), [(0,)])

    def test_candidate_feedback_rolls_back_as_one_transaction(self):
        candidate_path = self.root / "rollback-candidates.json"
        candidate_path.write_text(json.dumps({
            "candidates": [
                {"id": "a", "label": "Light", "text": "我们测量流量。"},
                {"id": "b", "label": "Bold", "text": "笔者观察流量。"},
            ],
            "chosen": "a",
        }, ensure_ascii=False))
        self.cli("reshow", self.db)
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER fail_phrase BEFORE INSERT ON phrase_prefs "
                "BEGIN SELECT RAISE(ABORT, 'simulated phrase failure'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            learn.choice_file(self.db, "results", "topic", str(candidate_path))
        self.assertEqual(self.raw("SELECT COUNT(*) FROM phrase_prefs"), [(0,)])
        self.assertEqual(self.raw("SELECT COUNT(*) FROM prefs"), [(0,)])

    def test_chinese_and_english_matching_reinforcement_and_short_text(self):
        for text in (CHINESE, "We measured diffusion in three samples.", "我"):
            learn.store(self.db, "author", text)
            learn.store(self.db, "author", text)
            match = json.loads(self.cli("nearest", self.db, text, 1).stdout)[0]
            self.assertEqual(match["text"], text)
            self.assertAlmostEqual(match["sim"], 1.0, places=4)
            self.assertEqual(match["rewards"], 2)
        self.assertEqual(len(learn.list_exemplars(self.db)), 3)

    def test_repeated_choices_reorder_then_reverse_without_context_leakage(self):
        options = ["Light", "Medium", "Bold", "Keep original"]
        with patch.object(learn, "_now_iso", return_value=NOW.isoformat()):
            for _ in range(3):
                learn.record(self.db, "results", "topic", "Bold")
            learned = learn.bias(self.db, "results", "topic", options)
            self.assertEqual(max(learned, key=lambda option: learned[option]["weight"]), "Bold")
            self.assertEqual(learned["Bold"]["picks"], 3)
            learn.signal(self.db, "results", "topic", "Bold")
            rejected = learn.bias(self.db, "results", "topic", options)
            self.assertLess(rejected["Bold"]["weight"], learned["Bold"]["weight"])
            self.assertEqual(rejected["Bold"]["picks"], 3)
            for _ in range(3):
                learn.record(self.db, "results", "topic", "Light")
            switched = learn.bias(self.db, "results", "topic", options)
            self.assertEqual(max(switched, key=lambda option: switched[option]["weight"]), "Light")
            self.assertEqual(set(switched), set(options))
            self.assertGreater(switched["Bold"]["weight"], 0)
            for section, role in (("methods", "topic"), ("results", "support")):
                unrelated = learn.bias(self.db, section, role, options)
                self.assertTrue(all(item == {"weight": 0, "picks": 0}
                                    for item in unrelated.values()))

    def test_stale_preferences_forget_while_heavy_and_fresh_survive(self):
        with patch.object(learn, "_now_iso", return_value=NOW.isoformat()):
            for _ in range(3):
                learn.record(self.db, "intro", "topic", "Medium")
            self.age("prefs", 400)
            learn.prune(self.db)
            self.assertEqual(self.raw("SELECT * FROM prefs"), [])
            learn.record(self.db, "intro", "topic", "Bold", 100000)
            self.age("prefs", 40)
            learn.record(self.db, "intro", "topic", "Light")
            learn.prune(self.db)
            self.assertEqual(len(self.raw("SELECT * FROM prefs")), 2)

    def fill_exemplars(self):
        with patch.object(learn, "_now_iso", return_value=NOW.isoformat()):
            for i in range(learn.MAX_EXEMPLARS):
                for _ in range(2):
                    learn.store(self.db, "author", f"Old author sentence number {i}.")
            self.age("exemplars", 400)

    def test_prune_forgets_old_reinforced_exemplars(self):
        self.fill_exemplars()
        with patch.object(learn, "_now_iso", return_value=NOW.isoformat()):
            learn.prune(self.db)
            self.assertEqual(learn.list_exemplars(self.db), [])
            learn.store(self.db, "author", "A new author sentence.")
            learn.prune(self.db)
            self.assertEqual(len(learn.list_exemplars(self.db)), 1)

    def test_capacity_admits_new_feedback_without_prune(self):
        self.fill_exemplars()
        with patch.object(learn, "_now_iso", return_value=NOW.isoformat()):
            keep = "Old author sentence number 0."
            learn.store(self.db, "author", keep)
            new = "We now prefer this new wording."
            learn.store(self.db, "author", new)
        rows = learn.list_exemplars(self.db)
        self.assertEqual(len(rows), learn.MAX_EXEMPLARS)
        self.assertTrue({keep, new}.issubset({r["text"] for r in rows}))

    def test_file_commands_preserve_arbitrary_manuscript_text(self):
        text = 'We estimate $k_B$; "quoted", author\'s \\alpha.\n`literal` $(literal) 中文。\n'
        path = self.root / "author sentence.txt"
        path.write_bytes(text.encode("utf-8"))
        self.cli("store-file", self.db, "author", path)
        stored = json.loads(self.cli("list", self.db, "author").stdout)
        self.assertEqual(stored[0]["text"], text)
        found = json.loads(self.cli("nearest-file", self.db, path, 1).stdout)
        self.assertEqual(found[0]["text"], text)
        self.assertAlmostEqual(found[0]["sim"], 1.0, places=4)

    def test_invalid_inputs_fail(self):
        for text in ("", "!!! ..."):
            self.cli("store", self.db, "author", text, success=False)
        self.cli("record", self.db, "intro", "topic", "Bold", 0, success=False)
        self.cli("record", self.db, "intro", "topic", "", success=False)
        self.cli("store-file", self.db, "author", self.root / "missing.txt", success=False)
        candidate = self.root / "invalid-candidates.json"
        candidate.write_text(json.dumps({
            "candidates": [{"id": "a", "label": "Light", "text": "我们测量。"}],
            "chosen": "missing",
        }, ensure_ascii=False))
        before = self.raw("SELECT name FROM sqlite_master WHERE type='table'")
        self.cli("choice-file", self.db, "results", "topic", candidate, success=False)
        self.assertEqual(self.raw("SELECT name FROM sqlite_master WHERE type='table'"), before)


if __name__ == "__main__":
    unittest.main()
