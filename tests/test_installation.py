"""Updating managed files must not overwrite the author's mutable memory."""

from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallationTests(unittest.TestCase):
    def install(self, target):
        p = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/install.py"),
                            "--target", str(target)], capture_output=True, text=True,
                           timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_first_install_then_update_preserves_memory_and_other_user_files(self):
        with tempfile.TemporaryDirectory(prefix="sonder-install-") as td:
            target = Path(td) / "skill with spaces"
            self.install(target)
            memory = target / "memory/style-profile.md"
            self.assertEqual(memory.read_bytes(), (ROOT / "memory/style-profile.md").read_bytes())
            author_memory = "作者确认：保留术语。\r\nKeep $k_B$ literally.\n".encode()
            memory.write_bytes(author_memory)
            other = target / "memory/author-notes.txt"
            other.write_text("A private note")
            (target / "library/learn.py").write_text("old program")
            self.install(target)
            self.assertEqual(memory.read_bytes(), author_memory)
            self.assertEqual(other.read_text(), "A private note")
            self.assertEqual((target / "library/learn.py").read_bytes(),
                             (ROOT / "library/learn.py").read_bytes())
            self.install(target)
            self.assertEqual(memory.read_bytes(), author_memory)
            checked = subprocess.run([sys.executable, '-I', str(target / 'scripts/check_runtime.py')],
                                     capture_output=True, text=True, cwd=td, timeout=20)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(json.loads(checked.stdout)['engine_path'], str((target / 'library/learn.py').resolve()))
            self.assertFalse(list(target.rglob('*.db')))

    def test_incompatible_engine_does_not_publish_new_skill_or_success(self):
        with tempfile.TemporaryDirectory(prefix='sonder-install-incompatible-') as td:
            root = Path(td)
            source = root / 'source'
            shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc'))
            (source / 'library/learn.py').write_text("import sys\nprint('unknown command', file=sys.stderr)\nsys.exit(1)\n")
            target = root / 'existing skill'
            (target / 'memory').mkdir(parents=True)
            (target / 'SKILL.md').write_bytes(b'Previous working Skill entry\n')
            memory = target / 'memory/style-profile.md'
            memory.write_bytes('原作者记忆。\r\n'.encode())
            before = memory.read_bytes()
            result = subprocess.run([sys.executable, '-I', str(source / 'scripts/install.py'),
                                     '--target', str(target)], capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn('runtime', result.stderr)
            self.assertNotIn('Installed Sonder', result.stdout)
            self.assertEqual((target / 'SKILL.md').read_bytes(), b'Previous working Skill entry\n')
            self.assertEqual(memory.read_bytes(), before)

    def test_update_follows_existing_skill_symlink_without_replacing_it(self):
        with tempfile.TemporaryDirectory(prefix="sonder-install-link-") as td:
            root = Path(td)
            real = root / "shared skill"
            (real / "memory").mkdir(parents=True)
            (real / "memory/style-profile.md").write_bytes(b"Existing author memory\n")
            link = root / "claude skill"
            link.symlink_to(real, target_is_directory=True)
            self.install(link)
            self.assertTrue(link.is_symlink())
            self.assertEqual((real / "memory/style-profile.md").read_bytes(),
                             b"Existing author memory\n")
            self.assertTrue((link / "library/learn.py").is_file())

    def test_source_directory_cannot_be_an_installation_target(self):
        for target in (ROOT, ROOT / "scripts/nested-install"):
            p = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/install.py"),
                                "--target", str(target)], capture_output=True, text=True,
                               timeout=20)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("outside the source repository", p.stderr)
        self.assertFalse((ROOT / "scripts/nested-install").exists())


if __name__ == "__main__":
    unittest.main()
