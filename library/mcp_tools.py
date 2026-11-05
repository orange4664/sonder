"""Transport-neutral, single-workspace access to the existing learning engine."""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_engine():
    return _load_module('sonder_mcp_engine', ROOT / 'library/learn.py')


class WorkspaceTools:
    """Keep model state in one process while every store operation uses core APIs."""

    def __init__(self, workspace):
        self.root = Path(workspace).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError('revision workspace must already exist and be a directory')
        self.engine = _load_engine()
        checker = _load_module('sonder_mcp_runtime', ROOT / 'scripts/check_runtime.py')
        checker._validate_runtime_info(self.engine.engine_info())
        self.instance_id = uuid.uuid4().hex
        self._lock = threading.RLock()

    def _file(self, name, *, existing=True, candidate_output=False, receipt_output=False):
        if not isinstance(name, str) or not name:
            raise ValueError('file path must be a non-empty string')
        path = Path(name).expanduser()
        if not path.is_absolute():
            path = self.root / path
        if receipt_output and path.is_symlink():
            raise ValueError('receipt output must not be a symbolic link')
        path = path.resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('file path must remain inside this revision workspace')
        candidate_root = self.root / 'candidate-inputs'
        if candidate_output and (path == candidate_root or not path.is_relative_to(candidate_root)):
            raise ValueError('binding output must be a file inside candidate-inputs')
        receipt_root = self.root / 'choice-receipts'
        if receipt_output and (path == receipt_root or not path.is_relative_to(receipt_root)):
            raise ValueError('receipt output must be a file inside choice-receipts')
        if path.is_dir() or (existing and not path.is_file()):
            raise ValueError(f'expected a file in the revision workspace: {path}')
        return path

    @contextmanager
    def _operation(self):
        # Stanza and optional similarity backends share mutable process state.
        # Serialize the whole operation, including path/config checks and model work.
        with self._lock:
            if not self.root.is_dir() or self.root.resolve() != self.root:
                raise ValueError('revision workspace changed; restart the server')
            database = self._file('preference.db', existing=False)
            if database.parent != self.root:
                raise ValueError('preference.db must remain at the workspace root')
            for name in (self.engine.BACKEND_FILE, self.engine.SYNTAX_BACKEND_FILE,
                         self.engine.SYNTAX_MODEL_FILE, self.engine.SYNTAX_MODEL_VERSION_FILE):
                self._file(name, existing=False)
            try:
                yield str(database)
            except SystemExit as error:
                # Legacy CLI matchers use SystemExit for missing optional deps.
                # A failed request must not terminate this long-lived service.
                raise ValueError(str(error)) from error

    @staticmethod
    def _context(section, role):
        if not isinstance(section, str) or not section.strip() or not isinstance(role, str) or not role.strip():
            raise ValueError('section and role must be non-empty strings')

    def status(self):
        with self._operation() as database:
            return {
                'workspace': str(self.root), 'database': database,
                'engine_path': str(ROOT / 'library/learn.py'),
                'instance_id': self.instance_id,
                'engine_info': self.engine.engine_info(),
                'configured_syntax_backend': self.engine.get_syntax_backend(database),
                'configured_similarity_backend': self.engine.get_backend(database),
            }

    def bind(self, draft_file, output_file):
        with self._operation():
            draft = self._file(draft_file)
            output = self._file(output_file, existing=False, candidate_output=True)
            return self.engine.bind_file(str(draft), str(output))

    def learning_policy(self, action=None):
        with self._operation() as database:
            return self.engine.learning_policy(database, action)

    def prepare(self, section, role, draft_file, output_file, summary=False):
        self._context(section, role)
        if type(summary) is not bool:
            raise ValueError('summary must be a boolean')
        with self._operation() as database:
            draft = self._file(draft_file)
            output = self._file(output_file, existing=False, candidate_output=True)
            return self.engine.prepare_file(database, section, role, str(draft), str(output), summary=summary)

    def nearest(self, sentence_file, limit=3, text_format=None):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError('limit must be an integer from 1 to 500')
        if text_format is not None:
            self.engine._validated_text_format(text_format)
        with self._operation() as database:
            text = self.engine._read_sentence(str(self._file(sentence_file)))
            return {'exemplars': self.engine.nearest(database, text, limit,
                                                   **({'text_format': text_format} if text_format is not None else {}))}

    def rank(self, section, role, candidate_file, summary=False):
        self._context(section, role)
        if type(summary) is not bool:
            raise ValueError('summary must be a boolean')
        with self._operation() as database:
            data, candidates = self.engine._candidate_data(str(self._file(candidate_file)))
            self.engine._require_candidate_context(data, section, role)
            ranked = self.engine.rank_candidates(database, section, role, candidates,
                                                 **({'text_format': data['text_format']} if 'text_format' in data else {}))
            return {'candidates': self.engine._rank_summary(ranked) if summary else ranked}

    def record_choice(self, section, role, candidate_file, round_id, receipt_file=None):
        self._context(section, role)
        self.engine._validated_round_id(round_id)
        with self._operation() as database:
            candidate = str(self._file(candidate_file))
            if receipt_file is not None:
                receipt = self._file(receipt_file, existing=False, receipt_output=True)
                return self.engine.choice_file_export(database, section, role, candidate, str(receipt),
                                                       expected_round_id=round_id)
            return self.engine.choice_file(database, section, role, candidate,
                                           expected_round_id=round_id)

    def prune(self):
        with self._operation() as database:
            self.engine.prune(database)
            return {'status': 'pruned'}
