"""Execute commands extracted from SKILL.md in independent, clean shells."""

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DocumentedCommandTests(unittest.TestCase):
    def test_other_merge_ranks_canonical_pool_without_rebinding_or_losing_alternatives(self):
        commands = dict(re.findall(r"<!-- sonder-command:(\w+) -->\s*```bash\n(.*?)```",
                                  (ROOT / 'SKILL.md').read_text(), re.DOTALL))
        data = json.loads((ROOT / 'tests/fixtures/coextensive-grammar.json').read_text())['training']
        data['edit_scope'] = {'sentence_ids': ['S001'], 'before_text': data['candidates'][0]['text'],
                              'keep_original_id': 'active'}
        with tempfile.TemporaryDirectory(prefix='sonder-other-doc-') as td:
            root = Path(td); db = root / 'preference.db'
            defaults = {'<learn-path>': str(ROOT / 'library/learn.py'), '<db-path>': str(db),
                        '<section>': 'methods', '<role>': 'detail',
                        '<receipt-file>': str(root / 'choice receipt.json')}

            def run(name, draft=None, candidate=None):
                replacements = {**defaults, '<draft-file>': str(draft), '<candidate-file>': str(candidate)}
                command = commands[name]
                for key, value in replacements.items(): command = command.replace(key, value)
                result = subprocess.run(['/bin/bash', '--noprofile', '--norc', '-c', command],
                                        cwd=root, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            base_draft, base_file = root / 'base draft.json', root / 'base canonical.json'
            base_draft.write_text(json.dumps(data, ensure_ascii=False))
            run('prepare', base_draft, base_file)
            base = json.loads(base_file.read_text()); base_bytes = base_file.read_bytes()
            other = deepcopy(data['candidates'][0])
            other.update(id='other', label='Other', text='研究者将样品清洗并干燥。')
            for frame in other['grammar']['frames']: frame['text'] = other['text'][:-1]
            temporary = {**data, 'candidates': [other], 'edit_scope': {
                k: v for k, v in data['edit_scope'].items() if k != 'keep_original_id'}}
            other_draft, other_file = root / 'other draft.json', root / 'other bound.json'
            other_draft.write_text(json.dumps(temporary, ensure_ascii=False))
            run('bind', other_draft, other_file)
            own = json.loads(other_file.read_text())['candidates'][0]
            merged = {**base, 'candidates': [*base['candidates'], own]}
            merged_file = root / 'complete canonical pool.json'
            merged_file.write_text(json.dumps(merged, ensure_ascii=False))
            original = merged_file.read_bytes(), merged_file.stat().st_mtime_ns
            ranked = run('other_rank', candidate=merged_file)
            self.assertEqual({r['id']: r['text'] for r in ranked},
                             {c['id']: c['text'] for c in merged['candidates']})
            self.assertEqual((merged_file.read_bytes(), merged_file.stat().st_mtime_ns), original)
            self.assertEqual(base_file.read_bytes(), base_bytes)
            self.assertEqual(merged['candidates'][:-1], base['candidates'])
            self.assertEqual(merged['edit_scope'], data['edit_scope'])
            self.assertIn('grammar_sha256', own['relation_coverage'])
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM choice_events').fetchone()[0], 0)
            decision = root / 'approved other.json'
            decision.write_text(json.dumps({**merged, 'chosen': 'other', 'author_owned': True}, ensure_ascii=False))
            first = run('choice', candidate=decision)
            self.assertFalse(first['replayed'])
            self.assertEqual(first['chosen'], 'other')
            with sqlite3.connect(db) as conn:
                before = list(conn.iterdump())
                self.assertEqual(conn.execute('SELECT text FROM exemplars').fetchall(), [(other['text'],)])
            self.assertTrue(run('choice', candidate=decision)['replayed'])
            with sqlite3.connect(db) as conn: self.assertEqual(list(conn.iterdump()), before)

    def test_documented_preparation_command_stops_on_conflicting_output(self):
        commands = dict(re.findall(r"<!-- sonder-command:(\w+) -->\s*```bash\n(.*?)```",
                                  (ROOT / 'SKILL.md').read_text(), re.DOTALL))
        doc = (ROOT / 'references/annotation-binding.md').read_text()
        data = json.loads(doc.split('<!-- sonder-binding-example -->', 1)[1].split('```json\n', 1)[1].split('```', 1)[0])
        with tempfile.TemporaryDirectory(prefix='sonder-prepare-doc-') as td:
            root = Path(td)
            draft, output, db = (root / name for name in ['draft file.json', 'canonical file.json', 'preference.db'])
            draft.write_text(json.dumps(data, ensure_ascii=False))
            command = commands['prepare']
            for key, value in {'<learn-path>': str(ROOT / 'library/learn.py'), '<db-path>': str(db),
                               '<section>': 'results', '<role>': 'support',
                               '<draft-file>': str(draft), '<candidate-file>': str(output)}.items():
                command = command.replace(key, value)
            def run():
                return subprocess.run(['/bin/bash', '--noprofile', '--norc', '-c', command],
                                      cwd=root, capture_output=True, text=True, timeout=20)
            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt['binding']['round_id'], data['round_id'])
            self.assertTrue(all(row['summary_version'] == 1 for row in receipt['candidates']))
            before = output.read_bytes(), output.stat().st_mtime_ns
            data['round_id'] = 'corrected-draft'
            draft.write_text(json.dumps(data, ensure_ascii=False))
            failed = run()
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(failed.stdout, '')
            self.assertEqual((output.read_bytes(), output.stat().st_mtime_ns), before)
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM choice_events').fetchone()[0], 0)

    def test_documented_binding_draft_runs_then_preserves_the_author_decision(self):
        commands = dict(re.findall(r"<!-- sonder-command:(\w+) -->\s*```bash\n(.*?)```",
                                  (ROOT / 'SKILL.md').read_text(), re.DOTALL))
        self.assertIn('bind', commands)
        doc = (ROOT / 'references/annotation-binding.md').read_text()
        data = json.loads(doc.split('<!-- sonder-binding-example -->', 1)[1].split('```json\n', 1)[1].split('```', 1)[0])
        with tempfile.TemporaryDirectory(prefix='sonder-binding-doc-') as td:
            folder = Path(td)
            draft = folder / 'semantic draft.json'
            target = folder / 'saved candidates.json'
            db = folder / 'preference.db'
            draft.write_text(json.dumps(data, ensure_ascii=False))
            command = commands['bind']
            for key, value in {'<learn-path>': str(ROOT / 'library/learn.py'),
                               '<draft-file>': str(draft), '<candidate-file>': str(target)}.items():
                command = command.replace(key, value)
            result = subprocess.run(['/bin/bash', '--noprofile', '--norc', '-c', command],
                                    cwd=folder, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(db.exists())
            bound = json.loads(target.read_text())
            self.assertEqual(bound['round_id'], data['round_id'])
            self.assertEqual(bound['edit_scope'], data['edit_scope'])
            self.assertIn('text_sha256', bound['candidates'][0]['analysis'])
            bound.update(chosen='keep', author_owned=True)
            target.write_text(json.dumps(bound, ensure_ascii=False))
            approved = target.read_bytes()
            args = [sys.executable, '-I', str(ROOT / 'library/learn.py'), 'choice-file',
                    str(db), 'results', 'support', str(target)]
            first = subprocess.run(args, capture_output=True, text=True, timeout=20)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertFalse(json.loads(first.stdout)['replayed'])
            self.assertEqual(json.loads(first.stdout)['edit_scope'], data['edit_scope'])
            collision = subprocess.run(['/bin/bash', '--noprofile', '--norc', '-c', command],
                                       cwd=folder, capture_output=True, text=True, timeout=20)
            self.assertNotEqual(collision.returncode, 0)
            self.assertEqual(target.read_bytes(), approved)
            with sqlite3.connect(db) as conn:
                before = list(conn.iterdump())
            replay = subprocess.run(args, capture_output=True, text=True, timeout=20)
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertTrue(json.loads(replay.stdout)['replayed'])
            with sqlite3.connect(db) as conn:
                self.assertEqual(list(conn.iterdump()), before)

    def test_literal_input_and_resume_commands_in_fresh_shells(self):
        commands = dict(re.findall(
            r"<!-- sonder-command:(\w+) -->\s*```bash\n(.*?)```",
            (ROOT / "SKILL.md").read_text(), re.DOTALL))
        with tempfile.TemporaryDirectory(prefix="sonder-docs-") as td:
            root = Path(td)
            skill = root / "installed skill"
            skill.mkdir()
            (skill / 'library').mkdir()
            engine = skill / "library/learn.py"
            for module in (ROOT / 'library').glob('*.py'):
                shutil.copy2(module, skill / 'library' / module.name)
            paper = root / "paper folder"
            paper.mkdir()
            revision = paper / "paper-revision"
            revision.mkdir()
            (revision / "revision-log.md").write_text("Existing revision to resume.\n")
            db = revision / "preference.db"
            receipts = revision / "choice-receipts"
            receipts.mkdir()
            receipt_file = receipts / "saved choice.json"
            sentence = revision / "current sentence.txt"
            text = 'We estimate $k_B$ and \\alpha.\r\n作者说 "keep it"; `literal` $(literal).\n'
            sentence.write_bytes(text.encode("utf-8"))
            candidate_file = revision / "candidate options.json"
            candidate_file.write_text(json.dumps({
                "round_id": "S001-documentation-decision",
                "edit_scope": {"sentence_ids": ["S001"], "before_text": text, "keep_original_id": "keep"},
                "candidates": [
                    {"id": "keep", "label": "Keep original", "text": text},
                    {"id": "light", "label": "Light", "text": text.replace("We estimate", "We measured")},
                ],
                "chosen": "keep",
                "author_owned": True,
            }, ensure_ascii=False))
            replacements = {"<learn-path>": str(engine), "<db-path>": str(db),
                            "<sentence-file>": str(sentence), "<section>": "intro",
                            "<role>": "topic", "<version>": "Keep original",
                            "<candidate-file>": str(candidate_file), "<receipt-file>": str(receipt_file)}
            outputs = {}
            self.assertIn('runtime', commands, 'Create and resume must check the installed runtime')
            (skill / 'scripts').mkdir()
            shutil.copy2(ROOT / 'scripts/check_runtime.py', skill / 'scripts/check_runtime.py')
            replacements['<runtime-check-path>'] = str(skill / 'scripts/check_runtime.py')
            for name in ("runtime", "learning_policy", "store", "nearest", "record", "bias", "rank", "choice", "prune", "resume", "syntax"):
                command = commands[name]
                for placeholder, value in replacements.items():
                    command = command.replace(placeholder, value)
                p = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", command],
                                   cwd=paper, env={"PATH": os.environ["PATH"]},
                                   capture_output=True, text=True, timeout=20)
                self.assertEqual(p.returncode, 0, f"{name}: {p.stderr}")
                outputs[name] = p.stdout
                if name == 'runtime':
                    self.assertFalse(db.exists())
                    self.assertEqual(json.loads(p.stdout)['status'], 'compatible')
                if name == 'learning_policy':
                    self.assertEqual(json.loads(p.stdout)['policy'], 'adaptive-v3')
            match = json.loads(outputs["nearest"])[0]
            self.assertEqual(match["text"], text)
            self.assertAlmostEqual(match["sim"], 1.0, places=4)
            self.assertGreaterEqual(json.loads(outputs["bias"])["Keep original"]["weight"], 990)
            ranked = json.loads(outputs["rank"])
            self.assertTrue(all(row['scoring_version'] == 5 for row in ranked))
            self.assertEqual(ranked[0]["id"], "keep")
            self.assertEqual(json.loads(outputs["choice"])["chosen"], "keep")
            self.assertTrue(json.loads(outputs["choice"])["idempotent"])
            exported = json.loads(receipt_file.read_text())
            self.assertEqual(exported['round_id'], 'S001-documentation-decision')
            self.assertFalse(exported['result']['replayed'])
            receipt_bytes = receipt_file.read_bytes()
            receipt_mtime = receipt_file.stat().st_mtime_ns
            self.assertEqual(json.loads(outputs["resume"])[0]["context"], "intro::topic")
            with sqlite3.connect(db) as conn:
                before_retry = list(conn.iterdump())
            choice_command = commands["choice"]
            for placeholder, value in replacements.items():
                choice_command = choice_command.replace(placeholder, value)
            retry = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", choice_command],
                                   cwd=paper, env={"PATH": os.environ["PATH"]},
                                   capture_output=True, text=True, timeout=20)
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertTrue(json.loads(retry.stdout)["replayed"])
            self.assertEqual(receipt_file.read_bytes(), receipt_bytes)
            self.assertEqual(receipt_file.stat().st_mtime_ns, receipt_mtime)
            with sqlite3.connect(db) as conn:
                self.assertEqual(list(conn.iterdump()), before_retry)
            conflicting = json.loads(candidate_file.read_text())
            conflicting["chosen"] = "light"
            candidate_file.write_text(json.dumps(conflicting))
            conflict = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", choice_command],
                                      cwd=paper, env={"PATH": os.environ["PATH"]},
                                      capture_output=True, text=True, timeout=20)
            self.assertNotEqual(conflict.returncode, 0)
            self.assertIn("round_id", conflict.stderr)
            with sqlite3.connect(db) as conn:
                self.assertEqual(list(conn.iterdump()), before_retry)


if __name__ == "__main__":
    unittest.main()
