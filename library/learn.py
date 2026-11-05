#!/usr/bin/env python3
"""
learn.py — context-scoped preference learning for sonder.

This is a direct port of the "gets to know you" mechanism used by Metasequoia IME
(user_dictionary_journal.cpp) and corroborated by libpinyin (pinyin_remember_user_input
+ pinyin_train), ZFVimIM (historical re-ranking), and Rime's user dictionary
(count `c` / decay `d` / timestamp `t`). It is extended with CBR-style exemplar
memory + age-decay, as in Rime/Letta-style hierarchical memory.

Four memory layers, with three active learning jobs plus legacy compatibility:

  1) `prefs` — weighted choice record. Every author choice is a weight on a
     (section::role, option) tuple. Reading applies EXPONENTIAL DECAY by age, so
     a preference you stopped reinforcing slowly fades instead of growing forever.
     Nothing is ever banned; a rejected option is only gentle-downgraded.

  2) `phrase_prefs` — context-scoped wording memory. Candidate text is broken
     into stable Chinese character n-grams and Latin tokens; accepted features
     rise and rejected features gently dip.

  3) `syntax_prefs` — optional structure memory. A local parser (or a
     conservative zero-dependency fallback) extracts versioned POS/dependency,
     connective-position and slot-template features plus an explicit academic
     argument layer (method/result/compare/hedge/limitation/conclusion moves).
     It is a soft secondary signal; it never becomes a grammar rule.

  4) `exemplars` — sentence memory. When the author KEEPS a sentence as-is, or
     types their own wording in "Other", that exact sentence is stored as a
     canonical example of their voice. `nearest()` retrieves the most similar
     stored sentences by cosine similarity, using one of three swappable backends
     (see # Similarity backends below). By default it's a zero-dependency
     character 3-gram TF-IDF matcher; optionally it uses sklearn TF-IDF or a
     `sentence-transformers` embedding model. The rewrites are then steered toward
     the author's own clean sentences, not toward a generic editor.

The clear distinction from a "tiny neural network": the personalized part is
still sparse, explicit online weights. A pretrained model, when enabled, only
extracts structure; it is not trained on the author's text. The ordinary
similarity layer generalizes by token overlap (character n-grams) instead of by
learned embeddings. Turning on the `embed` backend swaps in a pretrained
(published, not trained-on-your-text) embedding model — a quality upgrade, not a
new training step, and entirely optional.

Run via CLI. Store commands talk to the same SQLite database (preference.db).
`engine-info` reports code capabilities without opening a database or model.
New candidate JSON files include a persisted `round_id` for retry-safe choice
feedback. Reuse it for the same decision; use a new id for a new author choice.
Legacy files without it remain per-call feedback (idempotent=false).
Usage:
  python3 learn.py engine-info                           # no DB or model access
  python3 learn.py bind-file <draft-json> <output-json>    # bind exact fragments, no DB
  python3 learn.py prepare-file <db> <section> <role> <draft-json> <output-json> [--summary]
  python3 learn.py record <db> <section> <role> <opt> [weight=1000]
  python3 learn.py signal <db> <section> <role> <opt> [weight=1000]   # magnitude; always a dip
  python3 learn.py bias   <db> <section> <role> [options...]
  python3 learn.py store  <db> <kind> <sentence> [--text-format markdown] # store author sentence
  python3 learn.py store-file <db> <kind> <utf8-file> [--text-format markdown] # literal text, no shell expansion
  python3 learn.py nearest <db> <sentence> [n] [--text-format markdown] # similar author sentences
  python3 learn.py nearest-file <db> <utf8-file> [n] [--text-format markdown] # literal query text
  python3 learn.py rank-file <db> <section> <role> <json-file> [--summary] # rank real candidates
  python3 learn.py choice-file <db> <section> <role> <json-file> [--receipt-file FILE]
  python3 learn.py global <db> <opt> [weight=100000]        # legacy label weight in this DB only
  python3 learn.py list   <db> [kind]                       # view stored sentences
  python3 learn.py prune  <db> [stale_days=30] [min_weight=1000]
  python3 learn.py learning-policy <db> [legacy|adaptive-v1|adaptive-v2|adaptive-v3|ensure-adaptive]
  python3 learn.py reshow <db>
  python3 learn.py backend <db> [ngram|sklearn|embed]       # set/pick similarity backend
  python3 learn.py syntax-backend <db> [heuristic|stanza|off] [model_dir] [model_version]
"""

import hashlib
import importlib.util
import json
import math
import os
import re
import sqlite3
import stat
import string
import sys
import tempfile
import unicodedata
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

# Explicit sibling loading supports both python -I and file-based imports.
# Missing code must also make the no-database capability check fail clearly.
_adaptive_spec = importlib.util.spec_from_file_location(
    'sonder_adaptive_learning', Path(__file__).with_name('adaptive_learning.py'))
_adaptive = importlib.util.module_from_spec(_adaptive_spec)
_adaptive_spec.loader.exec_module(_adaptive)
for _adaptive_function in ('create_schema', 'policy', 'configure', 'weights_at', 'scores', 'update'):
    if not callable(getattr(_adaptive, _adaptive_function, None)):
        raise ImportError(f'incomplete adaptive learning module: {_adaptive_function}')
_relations = _adaptive._relative.features
for _relation_function in ('validate', 'bind_draft', 'project', 'state'):
    if not callable(getattr(_relations, _relation_function, None)):
        raise ImportError(f'incomplete relation feature module: {_relation_function}')
for _relative_function in ('readout', 'gradient', 'center_and_bound'):
    if not callable(getattr(_adaptive._relative, _relative_function, None)):
        raise ImportError(f'incomplete relative learning module: {_relative_function}')

# --- Tunables ---------------------------------------------------------------
FLOOR = 1                      # lowest managed weight
CEILING = 100_000_000          # highest managed weight
HALF_LIFE_DAYS = 30.0          # decay: weight halves every 30 days
MIN_WEIGHT = 1000              # below this an option is considered stale
EXEMPLAR_MIN_SIM = 0.0         # minimum cosine similarity to be worth returning
MAX_EXEMPLARS = 500            # cap on stored sentences (per paper)
SCHEMA_VERSION = 9             # injective context-key encoding; old rows preserved
CONTEXT_KEY_VERSION = 2
_CONTEXT_KEY_PREFIX = '@sonder-context-v2:'
_CONTEXT_TABLES = ('prefs', 'seen', 'phrase_prefs', 'syntax_prefs',
                   'adaptive_weights', 'adaptive_legacy', 'adaptive_legacy_mass',
                   'adaptive_opposition', 'adaptive_relative')
EXEMPLAR_INDEX_VERSION = 1     # bump when exemplar surface/count semantics change
RECEIPT_EXPORT_VERSION = 1     # durable original choice_events envelope
PHRASE_CHOICE_WEIGHT = 1000    # accepted candidate feature nudge
PHRASE_REJECT_WEIGHT = 250     # rejected candidate feature nudge
MAX_SYNTAX_FEATURES = 96
SYNTAX_CHOICE_WEIGHT = 1000    # accepted structural feature nudge
SYNTAX_REJECT_WEIGHT = 250     # rejected structural feature nudge
SYNTAX_AUTHOR_BONUS = 500      # author-owned wording is the strongest structure signal
SYNTAX_SCALE = 0.25            # structure is a soft secondary signal
SYNTAX_FEATURE_VERSION = "v3"
ANALYSIS_FEATURE_VERSION = "v2"
SCORING_VERSION = 2  # Independent means for base syntax and rhetorical analysis.
ANALYSIS_INPUT_VERSIONS = (1, 2)
ANALYSIS_PREFIX = f"syntax:analysis-{ANALYSIS_FEATURE_VERSION}:writing-model:"
ANALYSIS_TOPOLOGY_VERSION = "v1"
ANALYSIS_TOPOLOGY_PREFIX = f"syntax:analysis-topology-{ANALYSIS_TOPOLOGY_VERSION}:writing-model:"
GRAMMAR_FEATURE_VERSION = "v1"
GRAMMAR_INPUT_VERSIONS = (1, 2, 3, 4, 5, 6, 7)
GRAMMAR_PREFIX = f"syntax:grammar-{GRAMMAR_FEATURE_VERSION}:writing-model:"
MAX_GRAMMAR_FRAMES = 64
MAX_ANALYSIS_SPANS = 16
ANALYSIS_MOVES = frozenset({
    "background", "aim", "method", "result", "interpretation", "comparison",
    "contrast", "concession", "condition", "limitation", "conclusion", "unknown",
})
_ANALYSIS_GAP_RE = re.compile(r"[\s，,。.!！?？;；:：、…—\-]*\Z")

# Kinds for exemplars: author's own voice vs. one of my rewrite proposals.
KIND_AUTHOR = "author"   # author's own clean sentence they accepted / wrote
KIND_KEPT = "kept"       # a sentence I proposed and the author kept alive
KIND_REWRITE = "rewrite" # a sentence I rewrote and the author accepted

# --- Similarity backends ----------------------------------------------------
# Three swappable matchers for `nearest`. Default `ngram` is zero-dependency.
# `sklearn` and `embed` are OPT-IN (see `backend` command); they are lazily
# imported only when selected, so the default path never needs them.
BACKEND_FILE = ".similarity_backend"   # a sidecar file next to the db, per workspace
DEFAULT_BACKEND = "ngram"
EMBED_MODEL = "all-MiniLM-L6-v2"       # small, robust, multilingual-ish; 384-dim

# Syntax extraction is optional and never downloads a model implicitly. The
# standard-library heuristic is deliberately conservative; a local Stanza
# installation can be selected when the author has already provisioned a model.
SYNTAX_BACKEND_FILE = ".syntax_backend"
SYNTAX_MODEL_FILE = ".syntax_model_dir"
SYNTAX_MODEL_VERSION_FILE = ".syntax_model_version"
DEFAULT_SYNTAX_BACKEND = "heuristic"
SYNTAX_BACKENDS = ("heuristic", "stanza", "off")
STANZA_LANG = "zh-hans"
STANZA_PACKAGE = "default_fast"
STANZA_IDENTITY_VERSION = 1
_SYNTAX_PARSER_CACHE = {}


class SyntaxBackendUnavailable(RuntimeError):
    """A requested optional syntax backend cannot be used locally."""


def _backend_of(db):
    """Read the selected backend for this workspace, or the default."""
    path = os.path.join(os.path.dirname(db) or ".", BACKEND_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip() or DEFAULT_BACKEND
    except FileNotFoundError:
        return DEFAULT_BACKEND


def _set_backend(db, backend):
    if backend not in ("ngram", "sklearn", "embed"):
        raise ValueError("backend must be one of: ngram | sklearn | embed")
    path = os.path.join(os.path.dirname(db) or ".", BACKEND_FILE)
    with open(path, "w", encoding="utf-8") as f:
        f.write(backend)
    return backend


def _sidecar_value(db, filename, default=""):
    path = os.path.join(os.path.dirname(db) or ".", filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip() or default
    except FileNotFoundError:
        return default


def _syntax_backend_of(db):
    backend = _sidecar_value(db, SYNTAX_BACKEND_FILE, DEFAULT_SYNTAX_BACKEND)
    return backend if backend in SYNTAX_BACKENDS else DEFAULT_SYNTAX_BACKEND


def _syntax_model_dir_of(db):
    configured = _sidecar_value(db, SYNTAX_MODEL_FILE, "")
    return configured or os.environ.get("SONDER_SYNTAX_MODEL_DIR", "").strip()


def _syntax_model_version_of(db):
    return _sidecar_value(db, SYNTAX_MODEL_VERSION_FILE, "unversioned")


def _set_syntax_backend(db, backend, model_dir=None, model_version=None):
    if backend not in SYNTAX_BACKENDS:
        raise ValueError("syntax backend must be one of: heuristic | stanza | off")
    directory = os.path.dirname(db) or "."
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, SYNTAX_BACKEND_FILE), "w", encoding="utf-8") as f:
        f.write(backend)
    if model_dir is not None:
        model_dir = str(model_dir).strip()
        model_path = os.path.join(directory, SYNTAX_MODEL_FILE)
        if model_dir:
            with open(model_path, "w", encoding="utf-8") as f:
                f.write(model_dir)
        elif os.path.exists(model_path):
            os.unlink(model_path)
    if model_version is not None:
        model_version = str(model_version).strip()
        version_path = os.path.join(directory, SYNTAX_MODEL_VERSION_FILE)
        if model_version:
            with open(version_path, "w", encoding="utf-8") as f:
                f.write(model_version)
        elif os.path.exists(version_path):
            os.unlink(version_path)
    database_key = os.path.abspath(db)
    for key in list(_SYNTAX_PARSER_CACHE):
        if key[0] == database_key:
            _SYNTAX_PARSER_CACHE.pop(key, None)
    return backend


# --- Matchers ---------------------------------------------------------------
def _matcher_norm(text):
    """Normalized text for both TF-IDF matchers.

    Keeps Latin words/digits AND individual CJK characters, so Chinese (and
    Japanese/Korean) sentences participate in the character n-grams instead of
    being stripped to nothing. Each CJK char is kept as its own token; the
    3-gram window then spans neighbouring characters, which is the right unit
    for CJK where there are no word boundaries."""
    return " ".join(re.findall(r"[A-Za-z0-9]+|[぀-ヿ㐀-䶿一-鿿가-힯]",
                               text.lower()))


def _ngram_counts(text):
    """One feature definition for the stored index and query/document vectors."""
    t = _matcher_norm(text)
    if not t:
        return Counter()
    t = "  " + t + "  "
    return Counter(t[i:i + 3] for i in range(len(t) - 2) if t[i:i + 3].strip())


def _ngram_json(text):
    return json.dumps(_ngram_counts(text), ensure_ascii=False, sort_keys=True)


def _exemplar_surface(text, text_format=None):
    """Derived matcher input; raw author text and literal scientific content stay saved."""
    return _text_feature_views(text, **({'text_format': text_format} if text_format is not None else {}))[1]


def _ngram_counts_vec(counts, idf):
    if not counts:
        return {}
    weighted = {gram: count * idf.get(gram, 0.0) for gram, count in counts.items()}
    norm = math.sqrt(sum(value * value for value in weighted.values())) or 1.0
    return {gram: value / norm for gram, value in weighted.items()}


def _ngram_vec(text, df, idf):
    """Determine character 3-gram TF-IDF unit vector. Pure Python (default)."""
    return _ngram_counts_vec(_ngram_counts(text), idf)


def _sklearn_vecs(texts):
    """TF-IDF vectors via scikit-learn (char n-grams). Lazy import; only used
    when the `sklearn` backend is selected. Gives a clear actionable error if
    sklearn is missing instead of a raw traceback."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: WPS433
    except ImportError as e:
        raise SystemExit(
            "backend 'sklearn' needs `pip install scikit-learn`. Install it, or set "
            "backend back to 'ngram' with: python3 learn.py backend <db> ngram"
        ) from e
    vec = TfidfVectorizer(
        analyzer="char",
        ngram_range=(3, 3),
        lowercase=True,
        norm="l2",
        sublinear_tf=True,
    )
    return vec.fit_transform(texts)


def _embed_vecs(texts):
    """Sentence embeddings via a pretrained sentence-transformers model.
    Lazy import + first-time model download; only used with `embed` backend."""
    try:
        from sentence_transformers import SentenceTransformer  # noqa: WPS433
    except ImportError as e:
        raise SystemExit(
            "backend 'embed' needs `pip install sentence-transformers`. "
            "Install it, or set backend back to 'ngram' with: "
            "python3 learn.py backend <db> ngram"
        ) from e
    model = SentenceTransformer(EMBED_MODEL)
    return model.encode(texts, normalize_embeddings=True)


def _vectors_for(texts, backend):
    """Return (vectors, is_sparse) for a list of texts under the given backend.
    Open entries are 2D matrices; closed entries are row sparse matrices."""
    if backend == "embed":
        return _embed_vecs(texts), False
    if backend == "sklearn":
        return _sklearn_vecs(texts), True
    return texts, None   # ngram: raw text, handled per-pair below


def _cosine_sparse(a, b):
    """Cosine between two rows of a sparse matrix."""
    return float((a @ b.T).toarray()[0, 0])


def _cosine_dense(a, b):
    return float(sum(x * y for x, y in zip(a, b)))


def _clamp(w):
    return max(FLOOR, min(CEILING, w))


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _open(db):
    os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
    conn = sqlite3.connect(db)
    try:
        # Individual statements keep schema, data merge, index rebuild and the
        # version marker inside one transaction.
        conn.execute("BEGIN IMMEDIATE")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError(f"database version {version} needs a newer Sonder")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS prefs (
            context  TEXT NOT NULL,
            opt      TEXT NOT NULL,
            weight   INTEGER NOT NULL DEFAULT 0,
            picks    INTEGER NOT NULL DEFAULT 0,
            last_at  TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (context, opt)
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            context TEXT NOT NULL,
            opt     TEXT NOT NULL,
            at      TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (context, opt)
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS exemplars (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            kind     TEXT NOT NULL,
            text     TEXT NOT NULL,
            ngram    TEXT NOT NULL,
            created  TEXT NOT NULL,
            last_at  TEXT NOT NULL,
            rewards  INTEGER NOT NULL DEFAULT 0,
            text_format TEXT CHECK(text_format IS NULL OR text_format='markdown'),
            ngram_version INTEGER NOT NULL DEFAULT 0
        )""")
        exemplar_columns = {row[1] for row in conn.execute('PRAGMA table_info(exemplars)')}
        if 'text_format' not in exemplar_columns:
            conn.execute("ALTER TABLE exemplars ADD COLUMN text_format TEXT CHECK(text_format IS NULL OR text_format='markdown')")
        if 'ngram_version' not in exemplar_columns:
            conn.execute('ALTER TABLE exemplars ADD COLUMN ngram_version INTEGER NOT NULL DEFAULT 0')
        conn.execute("""
        CREATE TABLE IF NOT EXISTS phrase_prefs (
            context  TEXT NOT NULL,
            feature  TEXT NOT NULL,
            weight   INTEGER NOT NULL DEFAULT 0,
            picks    INTEGER NOT NULL DEFAULT 0,
            last_at  TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (context, feature)
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS syntax_prefs (
            context  TEXT NOT NULL,
            feature  TEXT NOT NULL,
            weight   INTEGER NOT NULL DEFAULT 0,
            picks    INTEGER NOT NULL DEFAULT 0,
            last_at  TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (context, feature)
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS choice_events (
            round_id      TEXT PRIMARY KEY NOT NULL,
            payload_hash  TEXT NOT NULL,
            result_json   TEXT NOT NULL,
            created       TEXT NOT NULL
        )""")
        _adaptive.create_schema(conn)
        _upgrade_exemplar_index(conn, merge_legacy=version < 1)
        if version < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    return conn


def _migrate_exemplars(conn):
    """Upgrade both pre-unique and unique-but-unversioned databases in place."""
    merged = {}
    rows = conn.execute(
        "SELECT id,kind,text,created,last_at,rewards FROM exemplars ORDER BY id"
    ).fetchall()
    for id_, kind, text, created, last, rewards in rows:
        key = (kind, text)
        if key not in merged:
            merged[key] = [id_, created, last, rewards]
            continue
        entry = merged[key]
        entry[1] = min(entry[1], created, key=_parse_time)
        entry[2] = max(entry[2], last, key=_parse_time)
        entry[3] += rewards
        conn.execute("DELETE FROM exemplars WHERE id=?", (id_,))
    for (_, text), (id_, created, last, rewards) in merged.items():
        conn.execute(
            "UPDATE exemplars SET ngram=?,created=?,last_at=?,rewards=? WHERE id=?",
            (_ngram_json(text), _parse_time(created).isoformat(),
             _parse_time(last).isoformat(), rewards, id_),
        )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS exemplars_kind_text "
                 "ON exemplars(kind,text)")


def _validate_exemplar_index_version(version):
    if type(version) is not int or version < 0:
        raise ValueError('invalid exemplar index version')
    if version > EXEMPLAR_INDEX_VERSION:
        raise ValueError(f'exemplar index version {version} needs a newer Sonder')


def _upgrade_exemplar_index(conn, *, merge_legacy=False):
    """Rebuild only derived caches; never infer a missing source format."""
    rows = conn.execute('SELECT id,text,text_format,ngram_version FROM exemplars').fetchall()
    for _, _, text_format, version in rows:
        if text_format is not None:
            _validated_text_format(text_format)
        # Check before the v0 merge too: a duplicate with a future cache cannot
        # silently disappear and make an unsupported index look downgradeable.
        _validate_exemplar_index_version(version)
    if merge_legacy:
        _migrate_exemplars(conn)
        rows = conn.execute('SELECT id,text,text_format,ngram_version FROM exemplars').fetchall()
    for id_, text, text_format, version in rows:
        # The v0 merger writes its historical raw cache. Always replace that
        # derived value, even when an unversioned dump already had new columns.
        if merge_legacy or version < EXEMPLAR_INDEX_VERSION:
            conn.execute('UPDATE exemplars SET ngram=?,ngram_version=? WHERE id=?',
                         (_ngram_json(_exemplar_surface(text, text_format)), EXEMPLAR_INDEX_VERSION, id_))


def _context(section, role):
    """Keep unambiguous historical keys; encode tuples that would otherwise alias."""
    section, role = str(section), str(role)
    legacy = f'{section}::{role}'
    boundary = legacy.find('::')
    # Search with overlap: a: / b and a / :b both produce a:::b.
    if boundary == len(section) and legacy.find('::', boundary + 1) == -1:
        return legacy
    # Every ordinary tuple key contains ::; this namespace never does.
    # JSON also keeps literal backslashes, quotes and Unicode distinct.
    pair = json.dumps([section, role], ensure_ascii=False, separators=(',', ':'))
    return _CONTEXT_KEY_PREFIX + pair.replace(':', r'\u003a')


def _legacy_context_alias(context):
    if not context.startswith(_CONTEXT_KEY_PREFIX) or '::' in context:
        return None
    try:
        pair = json.loads(context[len(_CONTEXT_KEY_PREFIX):])
    except ValueError:
        raise ValueError('invalid encoded context key') from None
    if (not isinstance(pair, list) or len(pair) != 2
            or not all(isinstance(item, str) for item in pair)
            or _context(*pair) != context):
        raise ValueError('invalid encoded context key')
    return f'{pair[0]}::{pair[1]}'


def _require_resolved_context(conn, context):
    """Never silently abandon or attribute rows from an old ambiguous namespace."""
    alias = _legacy_context_alias(context)
    if alias is None:
        return
    for table in _CONTEXT_TABLES:
        if conn.execute(f'SELECT 1 FROM {table} WHERE context=? LIMIT 1', (alias,)).fetchone():
            raise ValueError(f'ambiguous legacy context key {alias!r} has stored history; '
                             'preserve its rows and resolve their intended section/role '
                             'before new ranking or learning; historical receipt replay '
                             'and explicit no-learning choices remain available')


_CJK_RUN_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]+")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_TEX_CONTROL_RE = re.compile(r"\\([A-Za-z]+)\*?")
_TEX_ENV_RE = re.compile(r"\\(begin|end)\s*\{([^{}]+)\}")
_MATH_ENVIRONMENTS = {
    'math', 'displaymath', 'equation', 'equation*', 'align', 'align*',
    'aligned', 'alignat', 'alignat*', 'alignedat', 'gather', 'gather*',
    'gathered', 'multline', 'multline*', 'eqnarray', 'eqnarray*', 'split',
}
_LITERAL_TEX_ARGUMENTS = {
    'cite', 'citep', 'citet', 'citealt', 'citealp', 'citeauthor', 'citeyear',
    'citeyearpar', 'parencite', 'textcite', 'autocite', 'footcite', 'smartcite',
    'supercite', 'nocite', 'ref', 'eqref', 'autoref', 'cref', 'pageref',
    'vref', 'nameref', 'label', 'url', 'href',
}
_MULTICITE_TEX_COMMANDS = {'cites', 'parencites', 'textcites', 'autocites', 'footcites', 'smartcites'}
_TEXT_FORMAT_TEX_COMMANDS = {
    'emph', 'textbf', 'textit', 'textmd', 'textnormal', 'textrm',
    'textsf', 'texttt', 'textup', 'textsl', 'textsc', 'underline',
}


def _tex_group_end(text, start):
    """End of one balanced brace/bracket group; escaped delimiters are literal."""
    if start >= len(text) or text[start] not in '{[':
        return None
    stack = ['}' if text[start] == '{' else ']']
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == '\\':
            index += 2
            continue
        if char == '{':
            stack.append('}')
        elif char == '[' and stack[-1] == ']':
            stack.append(']')
        elif char == stack[-1]:
            stack.pop()
            if not stack:
                return index + 1
        index += 1
    return None


def _tex_argument_end(text, start):
    """Skip optional notes and one required argument, without swallowing prose."""
    index = start
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text) or text[index] not in '[{':
            return None
        end = _tex_group_end(text, index)
        if end is None:
            return None
        if text[index] == '{':
            return end
        index = end
    return None


def _math_region_end(text, start, opening, closing):
    index = start + len(opening)
    while index < len(text):
        if text[index] == '%':
            newline = text.find('\n', index)
            if newline < 0:
                return None
            index = newline + 1
            continue
        if text.startswith(closing, index):
            return index + len(closing)
        index += 2 if text[index] == '\\' else 1
    return None


def _math_environment_end(text, start, name):
    depth, index = 1, start
    while index < len(text):
        if text[index] == '%':
            newline = text.find('\n', index)
            if newline < 0:
                return None
            index = newline + 1
            continue
        if text[index] == '\\':
            match = _TEX_ENV_RE.match(text, index)
            if match:
                if match[2] == name:
                    depth += 1 if match[1] == 'begin' else -1
                    if not depth:
                        return match.end()
                index = match.end()
            else:
                index += 2
        else:
            index += 1
    return None


def _markdown_opaque_end(text, start):
    """Protect matched code spans and complete inline HTML/autolink syntax."""
    if text[start] not in '`<':
        return None
    if text[start] == '`':
        run = re.match(r'`+', text[start:])[0]
        for match in re.finditer(r'`+', text[start + len(run):]):
            if len(match[0]) == len(run):
                return start + len(run) + match.end()
    elif text.startswith('<!--', start):
        end = text.find('-->', start + 4)
        return end + 3 if end >= 0 else None
    elif (email := re.match(r'<[^<>\s]+@[^<>\s]+>', text[start:])) is not None:
        return start + email.end()
    elif re.match(r'<(?:/?[A-Za-z][A-Za-z0-9-]*(?=[\s/>])|[A-Za-z][A-Za-z0-9+.-]*:|[!?])', text[start:]):
        quote = None
        for index in range(start + 1, len(text)):
            char = text[index]
            if quote:
                if char == quote:
                    quote = None
            elif char in '\"\'':
                quote = char
            elif char == '>':
                return index + 1
    return None


def _markdown_destination_end(text, start):
    """Bound a parenthesized link target/title, keeping its raw contents opaque."""
    if start >= len(text) or text[start] != '(':
        return None
    depth, index, quote, angle = 1, start + 1, None, False
    while index < len(text):
        char = text[index]
        if char == '\\':
            index += 2
            continue
        if quote:
            if char == quote:
                quote = None
        elif angle:
            if char == '>':
                angle = False
        elif char == '<':
            angle = True
        elif char in '\"\'' and (index == start + 1 or text[index - 1].isspace()):
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _markdown_emphasis_positions(text, protected, labels):
    """Match CommonMark emphasis runs; return only consumed marker positions.

    Unicode flanking and the rule of three decide which runs may pair. Matched
    inner delimiters leave the stack, preventing crossing emphasis. Link labels
    have their own stack: an outer emphasis can surround a link but cannot end
    inside its label. Block rendering and other inline syntax are out of scope.
    """
    def punctuation(char):
        return char in string.punctuation or unicodedata.category(char).startswith(('P', 'S'))

    stacks, removed = {}, set()
    index, paragraph = 0, 0
    while index < len(text):
        if protected[index]:
            index += 1
            continue
        if text[index] == '\n' and re.match(r'\n[ \t]*\n', text[index:]):
            paragraph += 1
        if text[index] not in '*_':
            index += 1
            continue
        start, marker = index, text[index]
        while index < len(text) and text[index] == marker and not protected[index]:
            index += 1
        before = text[start - 1] if start else '\n'
        after = text[index] if index < len(text) else '\n'
        left = not after.isspace() and (not punctuation(after) or before.isspace() or punctuation(before))
        right = not before.isspace() and (not punctuation(before) or after.isspace() or punctuation(after))
        can_open = left and (marker == '*' or not right or punctuation(before))
        can_close = right and (marker == '*' or not left or punctuation(after))
        scope = max((begin for begin, end in labels if begin <= start < end), default=-1)
        stack = stacks.setdefault((paragraph, scope), [])
        closer = {'start': start, 'length': index - start, 'marker': marker,
                  'open': can_open, 'close': can_close}
        while closer['close'] and closer['length']:
            found = None
            for position in range(len(stack) - 1, -1, -1):
                opener = stack[position]
                if opener['marker'] != marker:
                    continue
                odd = ((opener['close'] or closer['open'])
                       and (opener['length'] + closer['length']) % 3 == 0
                       and (opener['length'] % 3 != 0 or closer['length'] % 3 != 0))
                if not odd:
                    found = position
                    break
            if found is None:
                break
            opener = stack[found]
            used = 2 if opener['length'] >= 2 and closer['length'] >= 2 else 1
            removed.update(range(opener['start'] + opener['length'] - used,
                                 opener['start'] + opener['length']))
            removed.update(range(closer['start'], closer['start'] + used))
            opener['length'] -= used
            closer['start'] += used
            closer['length'] -= used
            del stack[found + 1:]
            if not opener['length']:
                stack.pop()
        if closer['open'] and closer['length']:
            stack.append(closer)
    return removed


def _text_feature_views(text, *, text_format=None):
    """Return phrase prose and the surface for base syntax, without editing text.

    This is a bounded lexical guard, not a TeX renderer or fact classifier.
    Both views remove supported text-format wrappers. Only the phrase view
    masks literals with spaces; base syntax retains their original content.
    Skip complete literal regions so their inner controls are never expanded.
    Malformed groups never consume following prose.
    """
    prose = list(text)
    surface = list(text)
    markdown = text_format is not None
    if markdown:
        _validated_text_format(text_format)
    protected = bytearray(len(text)) if markdown else None
    brackets, labels = [], []
    index = 0
    while index < len(text):
        end = None
        if markdown:
            opaque_end = _markdown_opaque_end(text, index)
            if opaque_end is not None:
                protected[index:opaque_end] = b'\1' * (opaque_end - index)
                index = opaque_end
                continue
            if (text[index] == '\\' and index + 1 < len(text)
                    and text[index + 1] in string.punctuation
                    and not (text[index + 1] in '([' and _math_region_end(
                        text, index, text[index:index+2], '\\)' if text[index + 1] == '(' else '\\]') is not None)):
                # Keep the escaped marker literal, but remove its display escape.
                protected[index:index+2] = b'\1\1'
                prose[index] = surface[index] = ''
                index += 2
                continue
            if text[index] == '[':
                brackets.append(index)
            elif text[index] == ']' and brackets:
                begin = brackets.pop()
                destination_end = _markdown_destination_end(text, index + 1)
                if destination_end is not None:
                    labels.append((begin + 1, index))
                    protected[index+1:destination_end] = b'\1' * (destination_end - index - 1)
                    index = destination_end
                    continue
        if text[index] == '$':
            delimiter = '$$' if text.startswith('$$', index) else '$'
            end = _math_region_end(text, index, delimiter, delimiter)
        elif text[index] == '\\':
            opener = text[index:index+2]
            if opener in ('\\(', '\\['):
                end = _math_region_end(text, index, opener, '\\)' if opener == '\\(' else '\\]')
            else:
                control = _TEX_CONTROL_RE.match(text, index)
                if control:
                    command = control[1]
                    end = control.end()  # Control names themselves are markup.
                    if command in _TEXT_FORMAT_TEX_COMMANDS and not control[0].endswith('*'):
                        argument = end
                        while argument < len(text) and text[argument].isspace():
                            argument += 1
                        group_end = _tex_group_end(text, argument)
                        if group_end is not None and text[argument] == '{':
                            # Comments can hide a closing brace. Only ordinary
                            # text groups (including escaped symbols) are supported.
                            has_comment = any(match[0] == '%' for match in
                                              re.finditer(r'\\.|%', text[argument:group_end]))
                            if not has_comment:
                                # Keep original indices while removing the wrapper
                                # only; scan its body for nested wrappers/literals.
                                for view in (prose, surface):
                                    view[index:argument+1] = [''] * (argument + 1 - index)
                                    view[group_end-1] = ''
                                index = argument + 1
                                continue
                    elif command in ('begin', 'end'):
                        argument = end
                        while argument < len(text) and text[argument].isspace():
                            argument += 1
                        group_end = _tex_group_end(text, argument)
                        if group_end is not None and text[argument] == '{':
                            name = text[argument+1:group_end-1]
                            end = group_end
                            if command == 'begin' and name in _MATH_ENVIRONMENTS:
                                end = _math_environment_end(text, end, name) or end
                    elif command.lower() in _LITERAL_TEX_ARGUMENTS | _MULTICITE_TEX_COMMANDS:
                        argument_end = _tex_argument_end(text, end)
                        if argument_end is not None:
                            end = argument_end
                            if command.lower() in _MULTICITE_TEX_COMMANDS:
                                while (argument_end := _tex_argument_end(text, end)) is not None:
                                    end = argument_end
            if end is None:
                # Escaped dollar/braces and unknown control symbols are not
                # new delimiters. Their following prose still participates.
                index += 2
                continue
        if end is not None:
            prose[index:end] = ' ' * (end - index)
            if markdown:
                protected[index:end] = b'\1' * (end - index)
            index = end
        else:
            index += 1
    if markdown:
        for position in _markdown_emphasis_positions(text, protected, labels):
            prose[position] = surface[position] = ''
    return ''.join(prose), ''.join(surface)


def _phrase_prose(text, *, text_format=None):
    """Keep words contiguous while masking literal regions with separators."""
    return _text_feature_views(text, **({'text_format': text_format} if text_format is not None else {}))[0]


def _phrase_features(text, *, text_format=None):
    """Extract all distinct supported word/phrase features from candidate text.

    Chinese has no whitespace segmentation, so adjacent CJK bigrams/trigrams
    provide stable phrase fragments. Latin words and named alphanumeric terms
    are whole features. Pure numbers and explicit math/citation/reference
    payloads are not lexical taste; full sentence exemplars preserve them.
    """
    features = set()
    prose = _phrase_prose(text, **({'text_format': text_format} if text_format is not None else {}))
    for token in _WORD_RE.findall(prose.lower()):
        if len(token) >= 2 and token[0].isalpha():
            features.add("word:" + token)
    for run in _CJK_RUN_RE.findall(prose):
        for size in (2, 3):
            for start in range(len(run) - size + 1):
                features.add(f"cjk{size}:" + run[start:start + size])
    # A sorted-prefix quota favors CJK bigrams and can discard every Latin
    # term. It also turns present-but-discarded shared text into false rejection.
    # Features are finite by prose length; do not silently drop accepted evidence.
    return sorted(features)


# --- syntax features -------------------------------------------------------
_SYNTAX_PUNCT_RE = re.compile(r"[，,；;。！？!?：:]")
_CONNECTIVES = {
    "因此": "cause",
    "所以": "cause",
    "由于": "cause",
    "因为": "cause",
    "然而": "contrast",
    "但是": "contrast",
    "不过": "contrast",
    "但": "contrast",
    "而": "contrast",
    "此外": "addition",
    "同时": "addition",
    "首先": "sequence",
    "其次": "sequence",
    "最后": "sequence",
    "进一步": "sequence",
    "通过": "method",
    "采用": "method",
    "基于": "method",
    "结果": "result",
    "表明": "result",
    "说明": "result",
    "显示": "result",
    "相比": "compare",
    "相较": "compare",
}
# These are intentionally small, explicit, and auditable.  They describe
# common academic argument moves rather than pretending that a UD parser can
# infer a paper's rhetorical structure.  Longer cues must win over their
# substrings (for example, "结果表明" over "表明").
_ACADEMIC_CUES = (
    ("由此可见", "conclusion"),
    ("值得注意", "note"),
    ("结果表明", "result"),
    ("结果显示", "result"),
    ("结果发现", "result"),
    ("进一步", "extension"),
    ("此外", "extension"),
    ("同时", "extension"),
    ("相比", "compare"),
    ("相较", "compare"),
    ("然而", "contrast"),
    ("但是", "contrast"),
    ("不过", "contrast"),
    ("尽管", "contrast"),
    ("尚未", "limitation"),
    ("仍需", "limitation"),
    ("有待", "limitation"),
    ("局限", "limitation"),
    ("可能", "hedge"),
    ("或许", "hedge"),
    ("提示", "hedge"),
    ("似乎", "hedge"),
    ("综上", "conclusion"),
    ("因此", "conclusion"),
    ("可见", "conclusion"),
    ("通过", "method"),
    ("采用", "method"),
    ("基于", "method"),
    ("利用", "method"),
    ("表明", "result"),
    ("显示", "result"),
    ("发现", "result"),
)
# Nominal continuations of ambiguous cues. This is a conservative lexical
# guard for the demonstrated collisions, not a Chinese tokenizer or a claim
# of semantic disambiguation. Keep it shared by all surface-cue consumers.
_CUE_NOMINAL_CONTINUATIONS = {
    "可见": ("光", "度", "性"),
    "显示": ("器", "屏", "卡"),
    "通过": ("率",),
    "利用": ("率",),
    "采用": ("率",),
}
_HEURISTIC_MARKERS = tuple(sorted(
    set(_CONNECTIVES) | {
        "我们", "本文", "笔者", "是", "的", "了", "被", "将", "在", "对",
        "从", "到", "以", "和", "与", "并", "先", "再", "后", "则", "为", "于",
        "中", "其中",
    }, key=len, reverse=True))
_COARSE_UPOS = {
    "NOUN": "N", "PROPN": "N", "PRON": "PRON", "VERB": "V",
    "AUX": "AUX", "ADJ": "ADJ", "ADV": "ADV", "NUM": "NUM",
    "ADP": "ADP", "CCONJ": "CONJ", "SCONJ": "CONJ", "PART": "PART",
    "DET": "DET", "PUNCT": "PUNCT", "SYM": "SYM", "X": "X",
}
_CONTENT_UPOS = {"N", "V", "ADJ", "ADV", "NUM", "X"}


def _position_bucket(index, total):
    if total <= 1:
        return "only"
    ratio = index / max(1, total - 1)
    if ratio < 1 / 3:
        return "start"
    if ratio > 2 / 3:
        return "end"
    return "middle"


def _surface_cue_allowed(text, index, cue):
    """Exclude a cue embedded in a known nominal compound at this occurrence."""
    for stem, suffixes in _CUE_NOMINAL_CONTINUATIONS.items():
        if cue.endswith(stem) and text.startswith(suffixes, index + len(cue)):
            return False
    return True


def _heuristic_slot_template(text):
    """Keep grammatical markers while collapsing content into replaceable slots."""
    punctuation = set("，,；;。！？!?：:")
    pieces = []
    index = 0
    content_open = False
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        marker = next((item for item in _HEURISTIC_MARKERS
                       if text.startswith(item, index)
                       and _surface_cue_allowed(text, index, item)), None)
        if marker:
            if content_open:
                pieces.append("<X>")
                content_open = False
            pieces.append(marker)
            index += len(marker)
            continue
        if text[index] in punctuation:
            if content_open:
                pieces.append("<X>")
                content_open = False
            pieces.append(text[index])
            index += 1
            continue
        if not content_open:
            content_open = True
        index += 1
    if content_open:
        pieces.append("<X>")
    return " ".join(pieces)


def _syntax_feature_name(kind, value, namespace="heuristic"):
    if not re.fullmatch(rf"stanza-id{STANZA_IDENTITY_VERSION}-[0-9a-f]{{64}}", namespace):
        namespace = re.sub(r"[^A-Za-z0-9_.-]+", "_", namespace)[:48] or "unknown"
    return f"syntax:{SYNTAX_FEATURE_VERSION}:{namespace}:{kind}:{value}"


def _surface_cue_hits(text, cues):
    """Find non-overlapping, lexically guarded cues, longest match first."""
    candidates = []
    for cue, move in cues:
        start = 0
        while True:
            index = text.find(cue, start)
            if index < 0:
                break
            if _surface_cue_allowed(text, index, cue):
                candidates.append((index, index + len(cue), cue, move))
            start = index + max(1, len(cue))
    candidates.sort(key=lambda item: (-(item[1] - item[0]), item[0], item[2]))
    accepted = []
    for candidate in candidates:
        start, end, _, _ = candidate
        if any(start < other_end and end > other_start
               for other_start, other_end, _, _ in accepted):
            continue
        accepted.append(candidate)
    return sorted(accepted)


def _academic_cue_hits(text):
    return _surface_cue_hits(text, _ACADEMIC_CUES)


def _academic_clause_position(text, index):
    """Map a cue to a clause-relative bucket stable under word replacement."""
    clause_index = sum(1 for char in text[:index]
                       if _SYNTAX_PUNCT_RE.fullmatch(char))
    clause_total = max(1, len([part for part in _SYNTAX_PUNCT_RE.split(text)
                               if part.strip()]))
    return _position_bucket(clause_index, clause_total)


def _academic_slot_template(text, hits):
    """Collapse content while retaining argument moves and punctuation shape."""
    punctuation = set("，,；;。！？!?：:")
    pieces = []

    def append_content(segment):
        content_open = False
        for char in segment:
            if char.isspace():
                continue
            if char in punctuation:
                if content_open:
                    pieces.append("<X>")
                    content_open = False
                pieces.append(char)
            else:
                content_open = True
        if content_open:
            pieces.append("<X>")

    cursor = 0
    for start, end, _, move in hits:
        append_content(text[cursor:start])
        pieces.append(f"<{move.upper()}>")
        cursor = end
    append_content(text[cursor:])
    return " ".join(pieces)


def _academic_syntax_features(text):
    """Extract explainable paper-argument features independent of the parser."""
    text = str(text)
    hits = _academic_cue_hits(text)
    if not hits:
        return []
    features = set()
    moves = []
    for index, _, _, move in hits:
        moves.append(move)
        features.add(_syntax_feature_name(
            "academic_cue", f"{move}@{_academic_clause_position(text, index)}",
            "academic",
        ))
    features.add(_syntax_feature_name(
        "academic_move_seq", ">".join(moves), "academic"
    ))
    template = _academic_slot_template(text, hits)
    if template:
        features.add(_syntax_feature_name(
            "academic_slot_template", template, "academic"
        ))
    punctuation = []
    punctuation_names = {
        "，": "comma", ",": "comma", "；": "semicolon", ";": "semicolon",
        "。": "period", "！": "exclaim", "!": "exclaim",
        "？": "question", "?": "question", "：": "colon", ":": "colon",
    }
    for char in text:
        if char in punctuation_names:
            punctuation.append(punctuation_names[char])
    if punctuation:
        features.add(_syntax_feature_name(
            "academic_punct_shape", ".".join(punctuation), "academic"
        ))
    return sorted(features)


def _heuristic_syntax_features(text, namespace="heuristic"):
    """Return conservative structure cues without third-party packages.

    This fallback intentionally avoids pretending that raw CJK characters are
    a dependency tree. It records clause boundaries, punctuation, connective
    categories and replaceable non-CJK spans, so enabling syntax learning never
    makes the zero-dependency path brittle or overconfident.
    """
    text = str(text)
    features = set()
    punctuation = "".join(ch for ch in text if _SYNTAX_PUNCT_RE.fullmatch(ch))
    if punctuation:
        features.add(_syntax_feature_name("heuristic_punct", punctuation, namespace))
    clauses = [part for part in _SYNTAX_PUNCT_RE.split(text) if part.strip()]
    if clauses:
        lengths = ",".join(
            "short" if len(part) < 8 else "long" if len(part) > 20 else "mid"
            for part in clauses
        )
        features.add(_syntax_feature_name("heuristic_clauses", lengths, namespace))
    cue_hits = _surface_cue_hits(text, _CONNECTIVES.items())
    for index, _, cue, category in cue_hits:
        features.add(_syntax_feature_name(
            "cue_pos", f"{category}@{_position_bucket(index, max(1, len(text)))}", namespace
        ))
    if cue_hits:
        features.add(_syntax_feature_name(
            "cue_seq", ">".join(category for _, _, _, category in cue_hits), namespace
        ))
    template = _heuristic_slot_template(text)
    if template:
        features.add(_syntax_feature_name("heuristic_template", template, namespace))
    features.update(_academic_syntax_features(text))
    return sorted(features)[:MAX_SYNTAX_FEATURES]


def _stanza_resource_paths(config):
    """Map each model role to its actual files, preserving ensemble order."""
    paths = {}
    if not isinstance(config, dict):
        raise SyntaxBackendUnavailable("local stanza model configuration is unavailable")
    for key, value in config.items():
        if key.endswith("_path"):
            values = value if isinstance(value, (list, tuple)) else [value]
            files = tuple(os.path.realpath(os.path.abspath(os.path.expanduser(os.fspath(path))))
                          for path in values if isinstance(path, (str, os.PathLike)) and path)
            if files:
                paths[key] = files
    return paths


def _stanza_stat_state(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _stanza_resource_stamp(model_dir, config):
    """Cheap cache/load consistency check, separate from content identity."""
    paths = {os.path.join(model_dir, "resources.json")}
    for files in _stanza_resource_paths(config).values():
        paths.update(files)
    stamp = []
    for path in sorted(paths):
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            state = None
        except OSError as error:
            raise SyntaxBackendUnavailable(f"could not inspect local stanza resource {path}: {error}") from error
        else:
            state = _stanza_stat_state(stat)
        stamp.append((path, os.path.realpath(path), state))
    return tuple(stamp)


def _stanza_model_config(model_dir):
    """Resolve the same selected local resources as Pipeline, without loading."""
    from stanza.pipeline.core import build_default_config
    from stanza.resources.common import (add_dependencies, load_resources_json,
        maintain_processor_list, process_pipeline_parameters)
    lang, directory, package, processors = process_pipeline_parameters(
        STANZA_LANG, model_dir, STANZA_PACKAGE, "tokenize,pos,lemma,depparse")
    resources = load_resources_json(directory)
    if lang in resources and 'alias' in resources[lang]:
        lang = resources[lang]['alias']
    selected = maintain_processor_list(resources, lang, package, processors)
    selected = add_dependencies(resources, lang, selected)
    return build_default_config(resources, lang, directory, selected)


def _hash_stanza_model_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        before = _stanza_stat_state(os.fstat(stream.fileno()))
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
        if _stanza_stat_state(os.fstat(stream.fileno())) != before:
            raise SyntaxBackendUnavailable("local stanza model changed while hashing; retry when stable")
    return digest.hexdigest()


def _stanza_model_fingerprint(config, provider_version):
    paths = _stanza_resource_paths(config)
    required = {f'{name}_model_path' for name in ('tokenize', 'pos', 'lemma', 'depparse')}
    if not required.issubset(paths):
        raise SyntaxBackendUnavailable("cannot identify all local stanza processor model files")
    hashes = {}
    for files in paths.values():
        for path in files:
            if path not in hashes:
                hashes[path] = _hash_stanza_model_file(path)
    payload = {
        'version': STANZA_IDENTITY_VERSION, 'provider': 'stanza',
        'provider_version': provider_version, 'language': STANZA_LANG,
        'processors': ['tokenize', 'pos', 'lemma', 'depparse'],
        'artifacts': {role: [hashes[path] for path in files] for role, files in paths.items()},
        'options': {key: value for key, value in config.items() if not key.endswith('_path')},
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _stanza_pipeline(db):
    model_dir = _syntax_model_dir_of(db)
    if not model_dir:
        raise SyntaxBackendUnavailable(
            "syntax backend 'stanza' needs an existing local model directory; "
            "configure it with syntax-backend or SONDER_SYNTAX_MODEL_DIR"
        )
    model_dir = os.path.abspath(os.path.expanduser(model_dir))
    if not os.path.isdir(model_dir):
        raise SyntaxBackendUnavailable(f"syntax model directory does not exist: {model_dir}")
    try:
        import stanza
        from stanza import Pipeline  # noqa: WPS433
        try:
            from stanza import DownloadMethod  # noqa: WPS433
        except ImportError as error:
            raise SyntaxBackendUnavailable(
                "installed stanza lacks an explicit no-download mode"
            ) from error
    except ImportError as error:
        raise SyntaxBackendUnavailable(
            "syntax backend 'stanza' needs a local stanza install; "
            "the heuristic fallback remains available"
        ) from error
    provider_version = getattr(stanza, '__version__', None)
    if not isinstance(provider_version, str) or not provider_version.strip():
        raise SyntaxBackendUnavailable("local stanza provider version is unavailable; cannot identify model")
    model_version = _syntax_model_version_of(db)
    key = (os.path.abspath(db), model_dir, model_version, provider_version)
    cached = _SYNTAX_PARSER_CACHE.get(key)
    if cached is not None:
        parser, stamp = cached
        if _stanza_resource_stamp(model_dir, parser.config) == stamp:
            return parser
    # Sidecars or files may have changed outside this process. Only keep the
    # current parser for this database; the learned identity excludes its path/tag.
    for old_key in list(_SYNTAX_PARSER_CACHE):
        if old_key[0] == key[0]:
            _SYNTAX_PARSER_CACHE.pop(old_key, None)
    try:
        # An explicit model directory plus DownloadMethod.NONE prevents a
        # syntax query from silently reaching the network.
        no_download = getattr(DownloadMethod, "NONE", None)
        if no_download is None:
            raise SyntaxBackendUnavailable(
                "installed stanza lacks DownloadMethod.NONE; refusing implicit downloads"
            )
        parser_kwargs = {
            "lang": STANZA_LANG, "package": STANZA_PACKAGE,
            "processors": "tokenize,pos,lemma,depparse",
            "dir": model_dir, "verbose": False,
        }
        # Require Stanza's explicit no-download mode. If an older release does
        # not expose it, fall back rather than risking a hidden network call.
        parser_kwargs["download_method"] = no_download
        manifest_before = _stanza_resource_stamp(model_dir, {})
        config = _stanza_model_config(model_dir)
        stamp = _stanza_resource_stamp(model_dir, config)
        fingerprint = _stanza_model_fingerprint(config, provider_version)
        if (_stanza_resource_stamp(model_dir, {}) != manifest_before
                or _stanza_resource_stamp(model_dir, config) != stamp):
            raise SyntaxBackendUnavailable("local stanza resources changed while hashing; retry when stable")
        parser = Pipeline(**parser_kwargs)
        if (_stanza_resource_paths(parser.config) != _stanza_resource_paths(config)
                or {k: v for k, v in parser.config.items() if not k.endswith('_path')}
                != {k: v for k, v in config.items() if not k.endswith('_path')}
                or _stanza_resource_stamp(model_dir, parser.config) != stamp):
            raise SyntaxBackendUnavailable("local stanza resources changed while loading; retry when stable")
    except Exception as error:  # parser versions expose slightly different APIs
        raise SyntaxBackendUnavailable(f"could not load local stanza model: {error}") from error
    parser._sonder_model_version = model_version
    parser._sonder_model_fingerprint = fingerprint
    _SYNTAX_PARSER_CACHE[key] = (parser, stamp)
    return parser


def _stanza_syntax_features(text, db, diagnostics=None):
    parser = _stanza_pipeline(db)
    model_version = getattr(parser, "_sonder_model_version", None)
    if model_version is None:
        model_version = _syntax_model_version_of(db)
    fingerprint = getattr(parser, '_sonder_model_fingerprint', None)
    if not isinstance(fingerprint, str) or not re.fullmatch(r'[0-9a-f]{64}', fingerprint):
        raise SyntaxBackendUnavailable("local stanza parser has no verified model fingerprint")
    try:
        document = parser(text)
    except Exception as error:
        raise SyntaxBackendUnavailable(f"local stanza parsing failed: {error}") from error
    tokens = []
    surface_cursor = 0
    for sentence_index, sentence in enumerate(getattr(document, "sentences", [])):
        for token in getattr(sentence, "tokens", []):
            words = getattr(token, "words", [])
            if not words:
                continue
            word = words[0]
            token_id = getattr(word, "id", None)
            if not isinstance(token_id, int):
                continue
            upos = _COARSE_UPOS.get(getattr(word, "upos", None), "X")
            token_text = getattr(token, "text", "") or getattr(word, "text", "")
            start = getattr(token, "start_char", None)
            if (not isinstance(start, int) or start < surface_cursor
                    or text[start:start + len(token_text)] != token_text):
                start = text.find(token_text, surface_cursor) if token_text else -1
            if start >= 0:
                surface_cursor = start + len(token_text)
            tokens.append({
                "id": token_id,
                "sentence": sentence_index,
                "text": token_text,
                "start": start,
                "upos": upos,
                "head": getattr(word, "head", 0),
                "deprel": getattr(word, "deprel", "dep") or "dep",
            })
    if not tokens:
        raise SyntaxBackendUnavailable("local stanza parser returned no tokens")
    # Stanza restarts word ids at 1 in each sentence. Preserve document order
    # and resolve each dependency only within its own sentence.
    features = set()
    pos_seq = ",".join(token["upos"] for token in tokens)
    namespace = f"stanza-id{STANZA_IDENTITY_VERSION}-{fingerprint}"
    features.add(_syntax_feature_name("pos_seq", pos_seq, namespace))
    dep_seq = ",".join(token["deprel"] for token in tokens)
    features.add(_syntax_feature_name("dep_seq", dep_seq, namespace))
    edge_counts = Counter()
    by_id = {(token["sentence"], token["id"]): token for token in tokens}
    for token in tokens:
        parent = by_id.get((token["sentence"], token["head"]), {"upos": "ROOT"})
        edge_counts[f"{token['deprel']}:{parent['upos']}>{token['upos']}"] += 1
    for edge, count in sorted(edge_counts.items()):
        features.add(_syntax_feature_name("dep_shape", f"{edge}={count}", namespace))
    template = []
    for index, token in enumerate(tokens):
        token_text = token["text"]
        if token["upos"] == "PUNCT":
            template.append(token_text)
            continue
        category = _CONNECTIVES.get(token_text)
        if (category and token["start"] >= 0
                and _surface_cue_allowed(text, token["start"], token_text)):
            features.add(_syntax_feature_name(
                "cue_pos", f"{category}@{_position_bucket(index, len(tokens))}", namespace
            ))
            template.append(token_text)
        elif category:
            # A tokenizer can split 显示器 into 显示/器. Surface evidence must
            # still prevent that fragment from becoming a connective template.
            template.append("<X>")
        elif token["upos"] in _CONTENT_UPOS:
            template.append(f"<{token['upos']}>")
        elif token["upos"] == "PRON":
            template.append("<PRON>")
        else:
            template.append(token_text)
    features.add(_syntax_feature_name("slot_template", " ".join(template), namespace))
    # The academic layer uses the same formatting-transparent surface and a
    # stable namespace, so parser choice does not fragment learned rhetorical
    # preferences.  Stanza contributes the POS/dependency backbone only.
    features.update(_academic_syntax_features(text))
    if diagnostics is not None:
        diagnostics['model_version'] = model_version
        diagnostics['model_fingerprint'] = fingerprint
    return sorted(features)[:MAX_SYNTAX_FEATURES]


def _syntax_features(text, db, diagnostics=None, *, text_format=None):
    """Extract versioned syntax features with a safe local fallback."""
    backend = _syntax_backend_of(db)
    if diagnostics is not None:
        diagnostics.update(requested_backend=backend, fallback_reason=None,
                           model_version=None, model_fingerprint=None)
    if backend == "off":
        return [], "off"
    text = _text_feature_views(text, **({'text_format': text_format} if text_format is not None else {}))[1]
    if backend == "stanza":
        try:
            return _stanza_syntax_features(text, db, diagnostics), "stanza"
        except SyntaxBackendUnavailable as error:
            if diagnostics is not None:
                diagnostics['fallback_reason'] = str(error).strip()[:1000]
            return _heuristic_syntax_features(text), "heuristic-fallback"
    return _heuristic_syntax_features(text), "heuristic"


# --- decay ------------------------------------------------------------------
def _parse_time(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_days(last_at, now=None):
    now = now or _parse_time(_now_iso())
    try:
        last = _parse_time(last_at)
    except (ValueError, TypeError):
        return 0.0
    return max(0.0, (now - last).total_seconds() / 86400.0)


def _decay(last_at, now=None):
    """Multiplier in [0,1] — a preference halves every HALF_LIFE_DAYS. This is
    what makes the learning forget instead of accumulate forever."""
    return math.pow(0.5, _age_days(last_at, now) / HALF_LIFE_DAYS)


def _weight_of(conn, context, opt, now=None):
    r = conn.execute(
        "SELECT weight, picks, last_at FROM prefs WHERE context=? AND opt=?",
        (context, opt),
    ).fetchone()
    if not r:
        return 0, 0
    w, p, last = r
    return int(w * _decay(last, now)), p


# --- layer 1: weighted choices ---------------------------------------------
def _bump_tx(conn, context, opt, delta, picks_delta=1, now=None):
    """Apply legacy label feedback inside an already-open write transaction."""
    now = now or _parse_time(_now_iso())
    weight, picks = _weight_of(conn, context, opt, now)
    if delta < 0 and weight <= FLOOR:
        return  # No resurrection of an absent or already-faded preference.
    conn.execute(
        """
        INSERT INTO prefs(context, opt, weight, picks, last_at)
        VALUES(:ctx,:opt,:w,:p,:now)
        ON CONFLICT(context,opt) DO UPDATE SET
          weight=:w, picks=:p,
          last_at=:now
        """,
        {
            "ctx": context,
            "opt": opt,
            "w": _clamp(weight + delta),
            "p": picks + picks_delta,
            "now": now.isoformat(),
        },
    )


def _bump(conn, context, opt, delta, picks_delta=1):
    # Lock before reading, and materialize decay before resetting its clock.
    conn.execute("BEGIN IMMEDIATE")
    _require_resolved_context(conn, context)
    _bump_tx(conn, context, opt, delta, picks_delta)


def _phrase_weight_of(conn, context, feature, now=None):
    row = conn.execute(
        "SELECT weight, picks, last_at FROM phrase_prefs WHERE context=? AND feature=?",
        (context, feature),
    ).fetchone()
    if not row:
        return 0, 0
    weight, picks, last_at = row
    return int(weight * _decay(last_at, now)), picks


def _phrase_bump_tx(conn, context, feature, delta, picks_delta=0, now=None):
    """Update one extracted phrase inside an existing write transaction."""
    now = now or _parse_time(_now_iso())
    weight, picks = _phrase_weight_of(conn, context, feature, now)
    if delta < 0 and weight <= FLOOR:
        return
    conn.execute(
        """
        INSERT INTO phrase_prefs(context, feature, weight, picks, last_at)
        VALUES(:ctx,:feature,:weight,:picks,:now)
        ON CONFLICT(context,feature) DO UPDATE SET
          weight=:weight, picks=:picks, last_at=:now
        """,
        {
            "ctx": context,
            "feature": feature,
            "weight": _clamp(weight + delta),
            "picks": picks + picks_delta,
            "now": now.isoformat(),
        },
    )


def _syntax_weight_of(conn, context, feature, now=None):
    row = conn.execute(
        "SELECT weight, picks, last_at FROM syntax_prefs WHERE context=? AND feature=?",
        (context, feature),
    ).fetchone()
    if not row:
        return 0, 0
    weight, picks, last_at = row
    return int(weight * _decay(last_at, now)), picks


def _syntax_bump_tx(conn, context, feature, delta, picks_delta=0, now=None):
    """Update one versioned syntax feature in an existing transaction."""
    now = now or _parse_time(_now_iso())
    weight, picks = _syntax_weight_of(conn, context, feature, now)
    if delta < 0 and weight <= FLOOR:
        return
    conn.execute(
        """
        INSERT INTO syntax_prefs(context, feature, weight, picks, last_at)
        VALUES(:ctx,:feature,:weight,:picks,:now)
        ON CONFLICT(context,feature) DO UPDATE SET
          weight=:weight, picks=:picks, last_at=:now
        """,
        {
            "ctx": context,
            "feature": feature,
            "weight": _clamp(weight + delta),
            "picks": picks + picks_delta,
            "now": now.isoformat(),
        },
    )


def _round_feature_feedback_tx(conn, context, selected, alternatives, bump,
                               reward, penalty, now):
    """One observation per feature, independent of display order and duplicates.

    A feature present in the accepted sentence is positive evidence even if an
    alternative also contains it. Only features exclusive to unchosen texts
    receive a dip. Repeated alternatives do not multiply that evidence.
    """
    for feature in sorted(selected):
        bump(conn, context, feature, reward, 1, now)
    for feature in sorted(alternatives - selected):
        bump(conn, context, feature, -penalty, 0, now)


def _positive_weight(weight, what):
    """A weight magnitude must be a positive integer. 0 is NOT silently promoted
    to the default (the old `int(w) or 1000` did that); a caller passing 0 has
    made a mistake and should hear about it."""
    w = int(weight)
    if w <= 0:
        raise ValueError(f"{what} weight must be a positive integer, got {w}")
    return w


def record(db, section, role, opt, weight=1000):
    """The author picked `opt` in this context. Increase its weight/picks."""
    if not str(opt).strip():
        raise ValueError("record: option name is empty")
    weight = _positive_weight(weight, "record")
    with closing(_open(db)) as conn, conn:
        _bump(conn, _context(section, role), opt, weight, 1)


def signal(db, section, role, opt, weight=-1000):
    """The author declined `opt` here. Nudge it DOWN, never ban it. It can
    compete back up if the author later prefers it (this is the IME behavior).

    `weight` is the magnitude of the dip; its sign is ignored, so a caller who
    passes +500 by mistake still gets a DOWNWARD nudge. (Previously a positive
    value silently RAISED the rejected option.)"""
    if not str(opt).strip():
        raise ValueError("signal: option name is empty")
    dip = -_positive_weight(abs(int(weight)), "signal")
    with closing(_open(db)) as conn, conn:
        _bump(conn, _context(section, role), opt, dip, 0)


def global_pref(db, opt, weight=100_000):
    """Set a legacy option-label weight across contexts in this database.

    rank-file uses it only to break equal text-score ties; legacy bias includes
    it too. This does not save semantic instructions to a style profile or
    transfer preferences between papers.
    """
    with closing(_open(db)) as conn, conn:
        _bump(conn, "__GLOBAL__", opt, int(weight) or 100_000, 0)


def bias(db, section, role, options):
    """Soft ranking for `options` in this context, decay applied at read time.
    The caller uses this to ORDER its proposal (lead with higher weights)
    WITHOUT dropping any option."""
    ctx = _context(section, role)
    with closing(_open(db)) as conn:
        conn.execute('BEGIN')
        _require_resolved_context(conn, ctx)
        out = {}
        for opt in options:
            w, p = _weight_of(conn, ctx, opt)
            out[opt] = {"weight": w, "picks": p}
        g = conn.execute("SELECT opt, weight, last_at FROM prefs WHERE context='__GLOBAL__'").fetchall()
        for opt, w, last in g:
            if opt in out:
                out[opt]["weight"] = max(out[opt]["weight"], int(w * _decay(last)))
    return out


def _validate_analysis_attributes(value, kind):
    for key, allowed in (("move", ANALYSIS_MOVES),
                         ("polarity", {"affirmative", "negative", "unknown"}),
                         ("certainty", {"asserted", "hedged", "unknown"})):
        if not isinstance(value.get(key), str) or value[key] not in allowed:
            raise ValueError(f"analysis {kind} has invalid {key}")


def _validated_analysis_units(value, text):
    units = value['units']
    if not isinstance(units, list) or len(units) > MAX_ANALYSIS_SPANS:
        raise ValueError(f'analysis units must be an array of at most {MAX_ANALYSIS_SPANS} entries')
    normalized, all_parts = [], []
    for unit in units:
        if not isinstance(unit, dict) or set(unit) != {'move', 'polarity', 'certainty', 'parts'}:
            raise ValueError('analysis unit fields must be move, polarity, certainty and parts')
        _validate_analysis_attributes(unit, 'unit')
        parts = unit['parts']
        if not isinstance(parts, list) or not parts or len(all_parts) + len(parts) > MAX_ANALYSIS_SPANS:
            raise ValueError(f'analysis units require non-empty parts, at most {MAX_ANALYSIS_SPANS} parts in total')
        for part in parts:
            if not isinstance(part, dict) or set(part) != {'start', 'end', 'text'}:
                raise ValueError('analysis part fields must be start, end and text')
            try:
                _grammar_evidence(part, text)
            except ValueError as error:
                raise ValueError('analysis part: ' + str(error)) from error
        parts = sorted((dict(part) for part in parts), key=lambda part: part['start'])
        normalized.append({**unit, 'parts': parts})
        all_parts.extend(parts)
    all_parts.sort(key=lambda part: part['start'])
    previous_end = 0
    for part in all_parts:
        if part['start'] < previous_end:
            raise ValueError('analysis parts must not overlap within or across units')
        if not _ANALYSIS_GAP_RE.fullmatch(text[previous_end:part['start']]):
            raise ValueError('analysis units must cover all content; keep uncertain content unknown')
        previous_end = part['end']
    if all_parts and not _ANALYSIS_GAP_RE.fullmatch(text[previous_end:]):
        raise ValueError('analysis units must cover all content; keep uncertain content unknown')
    normalized.sort(key=lambda unit: unit['parts'][0]['start'])
    return {**value, 'units': normalized}


def _validated_analysis(value, text):
    """Bind model-supplied evidence to exact text; do not certify its meaning."""
    if not isinstance(value, dict):
        raise ValueError('analysis must be an object')
    if type(value.get('version')) is not int or value['version'] not in ANALYSIS_INPUT_VERSIONS:
        raise ValueError('analysis version must be integer 1 or 2')
    content = 'units' if value['version'] == 2 else 'spans'
    fields = {"version", "source", "text_sha256", content}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"analysis must contain exactly version, source, text_sha256, {content}")
    if value["source"] != "writing-model":
        raise ValueError("analysis source must be writing-model")
    if value["text_sha256"] != hashlib.sha256(text.encode("utf-8")).hexdigest():
        raise ValueError("analysis text_sha256 does not match the exact candidate text")
    if value['version'] == 2:
        return _validated_analysis_units(value, text)
    spans = value["spans"]
    if not isinstance(spans, list) or len(spans) > MAX_ANALYSIS_SPANS:
        raise ValueError(f"analysis spans must be an array with at most {MAX_ANALYSIS_SPANS} entries")
    normalized = []
    for span in spans:
        if not isinstance(span, dict) or set(span) != {
                "start", "end", "text", "move", "polarity", "certainty"}:
            raise ValueError("analysis span fields must be start, end, text, move, polarity, certainty")
        start, end = span["start"], span["end"]
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(text)):
            raise ValueError("analysis span requires valid Unicode codepoint start/end")
        if span["text"] != text[start:end] or _ANALYSIS_GAP_RE.fullmatch(text[start:end]):
            raise ValueError("analysis span text must match a non-empty content slice")
        _validate_analysis_attributes(span, 'span')
        normalized.append(dict(span))
    normalized.sort(key=lambda span: span["start"])
    previous_end = 0
    for span in normalized:
        if span["start"] < previous_end:
            raise ValueError("analysis spans must not overlap")
        if not _ANALYSIS_GAP_RE.fullmatch(text[previous_end:span["start"]]):
            raise ValueError("analysis must cover all content; mark uncertain spans unknown")
        previous_end = span["end"]
    if normalized and not _ANALYSIS_GAP_RE.fullmatch(text[previous_end:]):
        raise ValueError("analysis must cover all content; mark uncertain spans unknown")
    return {**value, "spans": normalized}


def _grammar_evidence(value, text):
    """Check exact Unicode evidence; this does not verify grammatical meaning."""
    start, end = value.get("start"), value.get("end")
    if (type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(text)):
        raise ValueError("grammar evidence requires valid Unicode start/end")
    if value.get("text") != text[start:end] or _ANALYSIS_GAP_RE.fullmatch(text[start:end]):
        raise ValueError("grammar evidence must match a non-empty content slice")
    return start, end


def _grammar_slot_roles(version, kind):
    """One versioned role inventory for draft bounds and canonical validation."""
    if kind == 'link':
        return {'head', 'dependent'}
    if kind != 'expression':
        return set()
    roles = {'agent', 'event', 'patient'}
    # Invalid versions still fail the canonical version check; do not coerce
    # strings/bools here while binding evidence from a draft.
    if type(version) is int and version >= 2:
        roles.add('complement')
    if type(version) is int and version >= 5:
        roles.add('recipient')
    return roles


def _validated_grammar(value, text):
    fields = {"version", "source", "text_sha256", "frames"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("grammar must contain exactly version, source, text_sha256, frames")
    if type(value["version"]) is not int or value["version"] not in GRAMMAR_INPUT_VERSIONS:
        raise ValueError("grammar version must be integer 1, 2, 3, 4, 5, 6 or 7")
    state_forms = value["version"] >= 2
    relations = {"condition", "relative", "complement", "cause", "concession", "unknown"}
    if value["version"] >= 3:
        relations.add("temporal")
    if value["version"] >= 4:
        relations.add("comparison")
    if value["source"] != "writing-model":
        raise ValueError("grammar source must be writing-model")
    if value["text_sha256"] != hashlib.sha256(text.encode("utf-8")).hexdigest():
        raise ValueError("grammar text_sha256 does not match candidate text")
    if not isinstance(value["frames"], list) or len(value["frames"]) > MAX_GRAMMAR_FRAMES:
        raise ValueError(f"grammar frames must be an array of at most {MAX_GRAMMAR_FRAMES} entries")
    frames, identities = [], {}
    for frame_index, frame in enumerate(value["frames"]):
        if not isinstance(frame, dict) or frame.get("kind") not in ("expression", "link"):
            raise ValueError("grammar frame kind must be expression or link")
        kind = frame["kind"]
        extra = {"form", "voice"} if kind == "expression" else {"relation"}
        if kind == "expression" and value["version"] >= 6:
            extra.add("slots_complete")
        if set(frame) != {"kind", "start", "end", "text", "slots"} | extra:
            raise ValueError("grammar frame fields do not match its kind")
        if "slots_complete" in frame and type(frame["slots_complete"]) is not bool:
            raise ValueError("grammar slots_complete must be a boolean")
        start, end = _grammar_evidence(frame, text)
        identity = (kind, start, end)
        if value['version'] < 7 and identity in identities:
            raise ValueError("grammar has duplicate frame kind/range: "
                             f"frames[{frame_index}] repeats frames[{identities[identity]}]")
        if value['version'] < 7:
            identities[identity] = frame_index
        forms = {"verbal", "nominalized", "unknown"}
        if state_forms:
            forms.update(("adjectival", "copular"))
        enums = (("form", forms),
                 ("voice", {"active", "passive", "nonagentive", "unknown"})) if kind == "expression" else (
                     ("relation", relations),)
        for field, allowed in enums:
            if not isinstance(frame[field], str) or frame[field] not in allowed:
                raise ValueError(f"grammar frame has invalid {field}")
        allowed_roles = _grammar_slot_roles(value['version'], kind)
        raw_slots = frame["slots"]
        if not isinstance(raw_slots, list) or len(raw_slots) > len(allowed_roles):
            raise ValueError("grammar slots must be a bounded array for this frame")
        slots, roles = [], set()
        for slot in raw_slots:
            if not isinstance(slot, dict) or set(slot) != {"role", "start", "end", "text"}:
                raise ValueError("grammar slot fields must be role, start, end, text")
            role = slot["role"]
            if not isinstance(role, str) or role not in allowed_roles or role in roles:
                raise ValueError("grammar slot roles must be valid and unique")
            slot_start, slot_end = _grammar_evidence(slot, text)
            if not start <= slot_start < slot_end <= end:
                raise ValueError("grammar slots must lie within their frame")
            roles.add(role); slots.append(dict(slot))
        slots.sort(key=lambda slot: slot["start"])
        if any(left["end"] > right["start"] for left, right in zip(slots, slots[1:])):
            raise ValueError("grammar slots within a frame must not overlap")
        if kind == "expression" and frame["form"] != "unknown" and "event" not in roles:
            raise ValueError("grammar expression with known form requires an event slot")
        if kind == "expression" and frame["form"] == "copular" and "complement" not in roles:
            raise ValueError("grammar copular expression requires a complement slot")
        if kind == "link" and frame["relation"] != "unknown" and roles != {"head", "dependent"}:
            raise ValueError("grammar known link requires head and dependent slots")
        normalized = {**frame, "slots": slots}
        if value['version'] >= 7:
            anchors = _relations.frame_key(normalized)[3]
            prior = identities.setdefault(identity, {})
            if prior and (not anchors or () in prior or anchors in prior):
                previous = prior.get(anchors, next(iter(prior.values())))
                raise ValueError('grammar has duplicate or unresolved coextensive frame anchors: '
                                 f'frames[{frame_index}] conflicts with frames[{previous}]')
            prior[anchors] = frame_index
        frames.append(normalized)
    frames.sort(key=lambda frame: (frame["start"], frame["end"], frame["kind"],
                                   _relations.frame_key(frame)[3]))
    covered = 0
    for frame in frames:
        if frame["start"] > covered and not _ANALYSIS_GAP_RE.fullmatch(text[covered:frame["start"]]):
            raise ValueError("grammar frames must cover content; use unknown for unsupported clauses")
        covered = max(covered, frame["end"])
    if frames and not _ANALYSIS_GAP_RE.fullmatch(text[covered:]):
        raise ValueError("grammar frames must cover content; use unknown for unsupported clauses")
    return {**value, "frames": frames}


def _grammar_observation_groups(grammar):
    """Group physical evidence without changing the saved frame objects."""
    groups = {}
    for frame in grammar['frames']:
        slots = frame['slots']
        ranges = tuple((slot['start'], slot['end']) for slot in slots)
        event = next((s for s in slots if s['role'] == 'event'), None)
        if frame['kind'] == 'expression' and event is not None:
            identity = ('expression', event['start'], event['end'], ranges)
        elif frame['kind'] == 'link' and len(slots) == 2:
            identity = ('link', ranges)
        else:
            # Missing anchors cannot establish equivalence with a bound frame.
            identity = ('unbound', frame['kind'], frame['start'], frame['end'],
                        tuple((s['role'], s['start'], s['end']) for s in slots))
        groups.setdefault(identity, []).append(frame)
    return list(groups.values())


def _grammar_observations(grammar):
    """Fieldwise consensus for features, with every original scope retained.

    Role disagreement or a partial inventory suppresses only shape evidence;
    independently agreed form/voice/relation may still contribute. The derived
    slots_complete on links is internal and is never accepted as input syntax.
    """
    observations = []
    for members in _grammar_observation_groups(grammar):
        first = members[0]
        projected = dict(first)
        fields = ('form', 'voice') if first['kind'] == 'expression' else ('relation',)
        for field in fields:
            values = {frame[field] for frame in members}
            projected[field] = next(iter(values)) if len(values) == 1 else 'unknown'
        roles = {tuple(slot['role'] for slot in frame['slots']) for frame in members}
        if first['kind'] == 'expression' or len(roles) != 1:
            projected['slots_complete'] = (len(roles) == 1 and
                all(frame.get('slots_complete', True) for frame in members))
        observations.append((projected, members))
    return observations


def _grammar_adjunct_evidence(grammar):
    """Keep one placement result, including abstention, for each possible adjunct.

    A compact predicate head comes after both a fronted and an embedded
    condition. Recover the distinction only when the expression evidence
    supplies it; no cue words or guessed omitted subjects are involved.
    """
    def content_overlaps(span, head):
        start, end = max(span['start'], head['start']), min(span['end'], head['end'])
        return start < end and not _ANALYSIS_GAP_RE.fullmatch(
            span['text'][start - span['start']:end - span['start']])

    observations = _grammar_observations(grammar)
    # Placement depends on every original expression scope, not a representative
    # wrapper. Conflicting forms stay unknown across all scopes of that event.
    expressions = [{**frame, 'form': observation['form']}
                   for observation, members in observations if observation['kind'] == 'expression'
                   for frame in members]
    evidence = []
    for link, _ in observations:
        if link['kind'] != 'link':
            continue
        if link['relation'] == 'unknown':
            evidence.append(('unknown', None))
            continue
        if link['relation'] not in {'condition', 'cause', 'concession', 'temporal', 'comparison'}:
            continue
        if not link.get('slots_complete', True):
            evidence.append((link['relation'], None))
            continue
        slots = {slot['role']: slot for slot in link['slots']}
        head, dependent = slots['head'], slots['dependent']
        positions = []
        for expression in expressions:
            event = next((s for s in expression['slots'] if s['role'] == 'event'), None)
            if event is None:
                # An unanchored unknown may still describe this predicate.
                # Its supplied scope cannot disappear and resolve a conflict.
                if expression['form'] == 'unknown' and content_overlaps(expression, head):
                    positions.append(None)
                continue
            if not head['start'] <= event['start'] < event['end'] <= head['end']:
                # Partial predicate/head overlap is unresolved attachment;
                # unrelated events and separator-only overlap are harmless.
                if content_overlaps(event, head):
                    positions.append(None)
                continue
            if expression['form'] == 'unknown':
                # Its predicate evidence still points into this head. Dropping
                # an uncertain competing scope must not resolve a conflict.
                positions.append(None)
                continue
            position = None
            if expression['start'] >= dependent['end']:
                position = 'before'
            elif expression['end'] <= dependent['start']:
                position = 'after'
            elif expression['start'] <= dependent['start'] < dependent['end'] <= expression['end']:
                left = any(s['end'] <= dependent['start'] for s in expression['slots'])
                right = any(s['start'] >= dependent['end'] for s in expression['slots'])
                if left and right:
                    position = 'embedded'
                elif right and _ANALYSIS_GAP_RE.fullmatch(expression['text'][:dependent['start'] - expression['start']]):
                    position = 'before'
                elif left and _ANALYSIS_GAP_RE.fullmatch(expression['text'][dependent['end'] - expression['start']:]):
                    position = 'after'
            positions.append(position)
        # Competing scopes or missing evidence must not create a confident key.
        if positions and positions[0] is not None and all(p == positions[0] for p in positions):
            evidence.append((link['relation'], positions[0]))
        else:
            evidence.append((link['relation'], None))
    return evidence


def _grammar_adjunct_positions(grammar, require_complete=False):
    evidence = _grammar_adjunct_evidence(grammar)
    if require_complete and any(position is None for _, position in evidence):
        return None  # Missing placement is not evidence against an alternative.
    return sorted({f'adjunct_position:{relation}:{position}'
                   for relation, position in evidence if position is not None})


def _adjunct_positions_comparable(candidates):
    """Only voice may remain unknown when comparing grounded placement.

    Placement uses event, role and scope evidence, not voice. Keep all other
    uncertainty guards, including projected conflicts between observations.
    """
    if not candidates:
        return False
    for candidate in candidates:
        grammar = candidate.get('grammar')
        if grammar is None or not grammar['frames']:
            return False
        for frame, _ in _grammar_observations(grammar):
            if not frame.get('slots_complete', True):
                return False
            field = 'form' if frame['kind'] == 'expression' else 'relation'
            if frame[field] == 'unknown':
                return False
        if _grammar_adjunct_positions(grammar, require_complete=True) is None:
            return False
    return True


def _grammar_feature_comparable(feature, grammar_ok, shape_ok, adjunct_ok, pair_ok):
    """One feature-family gate for compatibility and adaptive comparisons."""
    if feature.startswith(GRAMMAR_PREFIX + 'adjunct_position:'):
        return adjunct_ok
    return (grammar_ok and (shape_ok or not _is_argument_shape(feature))
            and (pair_ok or not feature.startswith(GRAMMAR_PREFIX + 'link_pair_order:')))


def _grammar_link_identities(grammar):
    """One relation observation per physical pair of bound slot ranges.

    Padded frame scopes do not create another relation. Labels and slot roles
    are deliberately outside identity: contradictory or unknown readings of
    the same evidence must abstain together, not become competing positive
    observations. Incomplete slots cannot be equated with a fully bound link.
    """
    identities = []
    for frame, _ in _grammar_observations(grammar):
        if frame['kind'] != 'link':
            continue
        slots = frame['slots']
        if len(slots) == 2:
            start, end = slots[0]['start'], slots[-1]['end']
            signature = (frame['relation'] + '/' + '>'.join(slot['role'] for slot in slots)
                         if frame['relation'] != 'unknown' and frame.get('slots_complete', True) else None)
        else:
            start, end, signature = frame['start'], frame['end'], None
        identities.append((start, end, signature))
    return sorted(identities, key=lambda item: (item[0], item[1]))


def _grammar_link_pair_evidence(grammar):
    """Order independent relations by their bound slots, not padded frames.

    Nested/overlapping relations and unresolved links supply no sequential key.
    Keep a zero-evidence position for each such pair: masking a relation must
    not improve the mean of the remaining known pairs. Keys omit words and
    rhetorical labels, and a set in the feature view prevents repeated pair
    patterns from multiplying their learned contribution.
    """
    links = _grammar_link_identities(grammar)
    evidence = []
    for index, left in enumerate(links):
        for right in links[index + 1:]:
            if left[2] is None or right[2] is None:
                evidence.append(None)
                continue
            if left[1] > right[0]:
                evidence.append(None)
                continue
            evidence.append('link_pair_order:' + left[2] + '||' + right[2])
    return evidence


def _grammar_features(grammar):
    if grammar is None:
        return []
    features = set()
    for frame, _ in _grammar_observations(grammar):
        order = ">".join(slot["role"] for slot in frame["slots"])
        if frame["kind"] == "expression":
            form, voice = frame["form"], frame["voice"]
            if form != "unknown":
                features.add(f"form:{form}")
                if frame.get("slots_complete", True):
                    features.add(f"expression_slots:{form}:{order}")
            if voice != "unknown":
                features.add(f"voice:{voice}")
            if form != "unknown" and voice != "unknown" and frame.get("slots_complete", True):
                features.add(f"expression:{form}/{voice}/{order}")
        elif frame["relation"] != "unknown":
            features.add(f"link:{frame['relation']}")
            if frame.get('slots_complete', True):
                features.add(f"link_order:{frame['relation']}:{order}")
    features.update(_grammar_adjunct_positions(grammar))
    features.update(key for key in _grammar_link_pair_evidence(grammar) if key is not None)
    return sorted(GRAMMAR_PREFIX + feature for feature in features)


def _grammar_status(candidate):
    grammar = candidate.get("grammar")
    if grammar is None:
        return "absent"
    if not _grammar_features(grammar):
        return "abstained"
    for frame, _ in _grammar_observations(grammar):
        fields = ("form", "voice") if frame["kind"] == "expression" else ("relation",)
        if any(frame[field] == "unknown" for field in fields) or not frame.get("slots_complete", True):
            return "partial"
    return "complete"


def _grammar_unknown_counts(grammar):
    """Unknown evidence contributes zero without shrinking a kind's divisor.

    Count each unresolved observation, not just a boolean flag: masking another
    distinct value must not improve a candidate that is already partial.
    These counts are scoring evidence only, never learned feature keys.
    """
    counts = Counter()
    if grammar is None:
        return counts
    for frame, _ in _grammar_observations(grammar):
        if frame['kind'] == 'expression':
            if frame['form'] == 'unknown':
                counts['form'] += 1
            if frame['form'] == 'unknown' or not frame.get('slots_complete', True):
                counts['expression_slots'] += 1
            if frame['voice'] == 'unknown':
                counts['voice'] += 1
            if frame['form'] == 'unknown' or frame['voice'] == 'unknown' or not frame.get('slots_complete', True):
                counts['expression'] += 1
        elif frame['relation'] == 'unknown':
            counts.update(('link', 'link_order'))
        elif not frame.get('slots_complete', True):
            counts['link_order'] += 1
    counts['adjunct_position'] = sum(position is None
                                    for _, position in _grammar_adjunct_evidence(grammar))
    counts['link_pair_order'] = sum(key is None for key in _grammar_link_pair_evidence(grammar))
    return counts


def _grammar_family_scores(features, matched, grammar=None):
    """Keep a learned clause link visible beside a shared expression frame.

    Averaging every grammatical feature together makes an extra learned
    relative/conditional link disappear when both candidates already match the
    same expression features. Average within each bounded family, then add the
    two family scores; frame duplication cannot inflate a family's score.
    Each family uses fixed feature kinds. Within a kind, unresolved frame
    evidence contributes zero alongside distinct known values, so uncertainty
    cannot improve the average of the remaining known evidence.
    """
    groups = {kind: [] for kind in ("form", "voice", "expression_slots", "expression", "link", "link_order", "adjunct_position", "link_pair_order")}
    weights = {item["feature"]: item["weight"] for item in matched}
    for feature in features:
        kind = feature[len(GRAMMAR_PREFIX):].split(":", 1)[0]
        groups[kind].append(weights.get(feature, 0))
    unknown = _grammar_unknown_counts(grammar)
    means = {kind: sum(values) / max(1, len(values) + unknown.get(kind, 0))
             for kind, values in groups.items()}
    return {"expression": sum(means[kind] for kind in ("form", "voice", "expression_slots", "expression")) / 4,
            "link": (means["link"] + means["link_order"]) / 2,
            # A separate additive family preserves every old score when no
            # new position weights have been learned; do not dilute link keys.
            "adjunct_position": means["adjunct_position"],
            # No old weights are divided by newly observed pair features.
            "link_pair_order": means["link_pair_order"]}


def _at_input_path(path, operation, *args):
    """Retain an input error's reason while identifying the caller's JSON field."""
    try:
        return operation(*args)
    except ValueError as error:
        raise ValueError(f'{path}: {error}') from None


def _validated_candidates(items):
    """Shared boundary for file commands and direct ranking callers."""
    if not isinstance(items, list):
        raise ValueError("candidate file must contain a candidates array")
    candidates = []
    seen = set()
    structure_by_text = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError("each candidate must be an object")
        if any(field in item for field in ('summary_version', 'omitted_fields', 'matched_feature_counts')):
            raise ValueError('rank summary rows are output only; load the complete saved candidate file')
        for field in ("learn_from_choice", "learning_context", "edit_scope", "text_format"):
            if field in item:
                raise ValueError(f"{field} belongs to the candidate file root, not an individual candidate")
        candidate_id = str(item.get("id", "")).strip()
        label = str(item.get("label", candidate_id)).strip()
        text = item.get("text")
        if not candidate_id or candidate_id in seen:
            raise ValueError("candidate ids must be non-empty and unique")
        if not isinstance(text, str) or not text.strip() or not _matcher_norm(text):
            raise ValueError("candidate text must contain matchable text")
        seen.add(candidate_id)
        candidate = {"id": candidate_id, "label": label, "text": text}
        if "analysis" in item:
            candidate["analysis"] = _at_input_path(
                f'candidates[{index}].analysis', _validated_analysis, item["analysis"], text)
        if "grammar" in item:
            candidate["grammar"] = _at_input_path(
                f'candidates[{index}].grammar', _validated_grammar, item["grammar"], text)
        if 'relation_coverage' in item:
            candidate['relation_coverage'] = _at_input_path(
                f'candidates[{index}].relation_coverage', _relations.validate,
                _grammar_observations, candidate, item['relation_coverage'])
        structure = (candidate.get("analysis"), candidate.get("grammar"), candidate.get('relation_coverage'))
        if text in structure_by_text and structure_by_text[text] != structure:
            raise ValueError("identical candidate text must use identical analysis, grammar and relation coverage")
        structure_by_text[text] = structure
        candidates.append(candidate)
    if not candidates:
        raise ValueError("candidate file must contain at least one candidate")
    return candidates


def _candidate_data(path):
    """Read and validate a candidate JSON file without shell interpolation."""
    with open(path, "r", encoding="utf-8") as source:
        data = json.load(source)
    return _validated_candidate_data(data)


def _validated_round_id(round_id):
    if (not isinstance(round_id, str) or not round_id or len(round_id) > 200
            or round_id.strip() != round_id):
        raise ValueError("round_id must be a non-empty string of at most 200 "
                         "characters without surrounding whitespace")
    return round_id


def _require_choice_round_id(data, expected_round_id):
    expected_round_id = _validated_round_id(expected_round_id)
    if data.get('round_id') != expected_round_id:
        raise ValueError("candidate round_id does not match the expected saved decision")


def _validated_learning_context(value):
    if (not isinstance(value, dict) or set(value) != {'section', 'role'}
            or any(not isinstance(item, str) or not item or item.strip() != item
                   for item in value.values())):
        raise ValueError("learning_context must contain exactly section and role as non-empty strings without surrounding whitespace")
    return value


def _require_candidate_context(data, section, role):
    """New envelopes freeze the context used to rank and learn one decision."""
    if 'learning_context' in data and data['learning_context'] != {'section': section, 'role': role}:
        raise ValueError("section and role must match the saved learning_context; preserve the original context when retrying a decision")


def _validated_edit_scope(value, candidates):
    """Freeze the declared source span, without guessing semantic equivalence."""
    required = {'sentence_ids', 'before_text'}
    if (not isinstance(value, dict) or not required <= set(value)
            or set(value) - (required | {'keep_original_id'})):
        raise ValueError('edit_scope requires sentence_ids and before_text; only keep_original_id is optional')
    ids = value['sentence_ids']
    if (not isinstance(ids, list) or not ids
            or any(not isinstance(item, str) or not item or item.strip() != item for item in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('edit_scope sentence_ids must be an ordered non-empty list of unique strings without surrounding whitespace')
    before = value['before_text']
    if not isinstance(before, str) or not before.strip():
        raise ValueError('edit_scope before_text must be a non-empty literal string')
    if 'keep_original_id' in value:
        keep = value['keep_original_id']
        if (not isinstance(keep, str) or not keep or keep.strip() != keep
                or not any(candidate['id'] == keep and candidate['text'] == before for candidate in candidates)):
            raise ValueError('edit_scope keep_original_id must identify a candidate exactly equal to before_text')
    return value


def _validated_text_format(value):
    if not isinstance(value, str) or value != 'markdown':
        raise ValueError('text_format must be the string markdown when present')
    return value


def _validated_candidate_data(data):
    """Validate shared document metadata without performing file/store IO."""
    if not isinstance(data, dict):
        raise ValueError("candidate file must contain a candidates array")
    if 'text_format' in data:
        _validated_text_format(data['text_format'])
    candidates = _validated_candidates(data.get("candidates"))
    chosen = data.get("chosen")
    if chosen is not None and str(chosen) not in {candidate["id"] for candidate in candidates}:
        raise ValueError("chosen candidate id is not present")
    if "author_owned" in data and not isinstance(data["author_owned"], bool):
        raise ValueError("author_owned must be a JSON boolean")
    if "learn_from_choice" in data and type(data["learn_from_choice"]) is not bool:
        raise ValueError("learn_from_choice must be a JSON boolean")
    if 'learning_context' in data:
        _validated_learning_context(data['learning_context'])
    if "round_id" in data:
        _validated_round_id(data['round_id'])
    if 'edit_scope' in data:
        _validated_edit_scope(data['edit_scope'], candidates)
        if 'round_id' not in data:
            raise ValueError('edit_scope requires a saved round_id')
    return data, candidates


def _bind_evidence(text, evidence, scope_start=0, scope_end=None):
    """Resolve literal fragments, refusing to guess among repeated matches."""
    if not isinstance(evidence, dict) or not isinstance(evidence.get('text'), str) or not evidence['text']:
        raise ValueError('binding evidence requires non-empty exact text')
    scope_end = len(text) if scope_end is None else scope_end
    fragment = evidence['text']
    if 'start' in evidence:
        if 'occurrence' in evidence:
            raise ValueError('binding evidence must not combine start and occurrence')
        start = evidence['start']
        if type(start) is not int:
            raise ValueError('binding start must be an absolute integer Unicode offset')
    else:
        if 'end' in evidence:
            raise ValueError('binding end requires an explicit start')
        matches, cursor = [], scope_start
        while True:
            found = text.find(fragment, cursor, scope_end)
            if found < 0:
                break
            matches.append(found)
            cursor = found + 1  # Overlapping matches are ambiguous too.
        if not matches:
            raise ValueError(f'binding fragment does not occur inside its scope: {fragment!r}')
        occurrence = evidence.get('occurrence')
        if 'occurrence' in evidence:
            if type(occurrence) is not int or not 1 <= occurrence <= len(matches):
                raise ValueError('binding occurrence must be a valid 1-based integer within its scope')
            start = matches[occurrence - 1]
        elif len(matches) == 1:
            start = matches[0]
        else:
            raise ValueError(f'ambiguous binding fragment ({len(matches)} matches); provide occurrence or start: {fragment!r}')
    end = start + len(fragment)
    if (not scope_start <= start < end <= scope_end or text[start:end] != fragment
            or ('end' in evidence and (type(evidence['end']) is not int or evidence['end'] != end))):
        raise ValueError('binding coordinates must match the exact text inside their scope')
    return {**{k: v for k, v in evidence.items() if k != 'occurrence'}, 'start': start, 'end': end}


def _grammar_draft_version(value):
    """Only new syntax changes an omitted version; old drafts retry byte-exactly."""
    frames = value.get('frames')
    if isinstance(frames, list) and any(isinstance(frame, dict) and 'slots_complete' in frame
                                        for frame in frames):
        return 6
    if isinstance(frames, list) and any(
            isinstance(frame, dict) and isinstance(frame.get('slots'), list)
            and any(isinstance(slot, dict) and slot.get('role') == 'recipient' for slot in frame['slots'])
            for frame in frames):
        return 5
    if isinstance(frames, list) and any(isinstance(frame, dict) and frame.get('relation') == 'comparison'
                                        for frame in frames):
        return 4
    if isinstance(frames, list) and any(isinstance(frame, dict) and frame.get('relation') == 'temporal'
                                        for frame in frames):
        return 3
    return 2


def _bind_annotation(value, text, kind, path=None):
    path = path or kind
    if not isinstance(value, dict):
        raise ValueError(f'{path}: {kind} draft must be an object')
    annotation = dict(value)
    annotation.setdefault('version', (2 if 'units' in value else 1) if kind == 'analysis' else _grammar_draft_version(value))
    annotation.setdefault('source', 'writing-model')
    annotation.setdefault('text_sha256', hashlib.sha256(text.encode('utf-8')).hexdigest())
    units = kind == 'analysis' and annotation['version'] == 2
    key = ('units' if units else 'spans') if kind == 'analysis' else 'frames'
    limit = MAX_ANALYSIS_SPANS if kind == 'analysis' else MAX_GRAMMAR_FRAMES
    rows = annotation.get(key)
    if not isinstance(rows, list) or len(rows) > limit:
        raise ValueError(f'{path}: {kind} draft requires a {key} array with at most {limit} entries')
    bound = []
    part_count = 0
    for index, row in enumerate(rows):
        row_path = f'{path}.{key}[{index}]'
        if units:
            if not isinstance(row, dict) or not isinstance(row.get('parts'), list) or not row['parts']:
                raise ValueError(f'{row_path}: analysis unit draft requires non-empty parts')
            part_count += len(row['parts'])
            if part_count > MAX_ANALYSIS_SPANS:
                raise ValueError(f'{row_path}: analysis draft permits at most {MAX_ANALYSIS_SPANS} total parts')
            bound.append({**row, 'parts': [_at_input_path(
                f'{row_path}.parts[{part_index}]', _bind_evidence, text, part)
                for part_index, part in enumerate(row['parts'])]})
            continue
        entry = _at_input_path(row_path, _bind_evidence, text, row)
        if kind == 'grammar':
            slots = entry.get('slots')
            if (not isinstance(slots, list)
                    or len(slots) > len(_grammar_slot_roles(annotation['version'], entry.get('kind')))):
                raise ValueError(f'{row_path}: grammar draft requires a bounded slots array')
            entry['slots'] = [_at_input_path(
                f'{row_path}.slots[{slot_index}]', _bind_evidence,
                text, slot, entry['start'], entry['end'])
                for slot_index, slot in enumerate(slots)]
        bound.append(entry)
    annotation[key] = bound
    return annotation


def bind_candidate_data(data):
    """Bind a strict semantic draft to the existing canonical candidate protocol.

    The caller supplies the interpretations and a persisted round ID. Empty
    arrays explicitly abstain. No parser/model or author store is consulted.
    """
    allowed = {'candidates', 'round_id', 'chosen', 'author_owned', 'learn_from_choice', 'learning_context', 'edit_scope', 'text_format', 'metadata'}
    if not isinstance(data, dict) or set(data) - allowed or not {'candidates', 'round_id'} <= set(data):
        raise ValueError('binding draft requires candidates and round_id; only chosen, author_owned, learn_from_choice, learning_context, edit_scope, text_format, metadata are optional')
    if 'metadata' in data and not isinstance(data['metadata'], dict):
        raise ValueError('binding metadata must be an object')
    if not isinstance(data['candidates'], list) or not data['candidates']:
        raise ValueError('binding candidates must be a non-empty array')
    candidates = []
    for index, item in enumerate(data['candidates']):
        if (not isinstance(item, dict) or set(item) - {'id', 'label', 'text', 'analysis', 'grammar', 'relation_coverage'}
                or not {'id', 'text', 'analysis', 'grammar'} <= set(item)):
            raise ValueError('binding candidate requires id, text, analysis and grammar; label and relation_coverage are optional')
        text = item['text']
        if not isinstance(text, str):
            raise ValueError('binding candidate text must be a string')
        candidate = {**item, **{kind: _bind_annotation(item[kind], text, kind, f'candidates[{index}].{kind}')
                               for kind in ('analysis', 'grammar')}}
        if 'relation_coverage' in item:
            original_frames = candidate['grammar']['frames']
            candidate['grammar'] = _at_input_path(
                f'candidates[{index}].grammar', _validated_grammar, candidate['grammar'], text)
            candidate['relation_coverage'] = _at_input_path(
                f'candidates[{index}].relation_coverage', _relations.bind_draft,
                _grammar_observations, candidate, original_frames, item['relation_coverage'])
        candidates.append(candidate)
    result, candidates = _validated_candidate_data({**data, 'candidates': candidates})
    return {**result, 'candidates': candidates}


def _publish_bound_file(content, output_path):
    """Publish complete bytes without replacing an existing decision file."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=os.path.dirname(output_path), prefix='.sonder-bind-', delete=False) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, output_path)
        except FileExistsError:
            with open(output_path, 'rb') as existing:
                if existing.read() == content:
                    return True
            raise ValueError('binding output already exists with different content; use a new output path') from None
        return False
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _publish_candidate_data(bound, output_path):
    """One byte/receipt format for binding alone and checked preparation."""
    content = (json.dumps(bound, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    output_path = os.path.abspath(os.fspath(output_path))
    reused = _publish_bound_file(content, output_path)
    return {'output': output_path, 'round_id': bound['round_id'], 'candidates': len(bound['candidates']), 'reused': reused}


def bind_file(input_path, output_path):
    with open(input_path, 'r', encoding='utf-8') as source:
        draft = json.load(source)
    return _publish_candidate_data(bind_candidate_data(draft), output_path)


def prepare_file(db, section, role, input_path, output_path, *, summary=False):
    """Publish this bound draft, then rank it; never fall through to an old file.

    Publication and ranking are not a cross-resource transaction. If ranking
    fails, the complete canonical remains available for an identical retry.
    """
    if type(summary) is not bool:
        raise ValueError('summary must be a boolean')
    _validated_learning_context({'section': section, 'role': role})
    with open(input_path, 'r', encoding='utf-8') as source:
        bound = bind_candidate_data(json.load(source))
    _require_candidate_context(bound, section, role)
    binding = _publish_candidate_data(bound, output_path)
    ranked = rank_candidates(db, section, role, bound['candidates'],
                             **({'text_format': bound['text_format']} if 'text_format' in bound else {}))
    return {'preparation_version': 1, 'binding': binding,
            'candidates': _rank_summary(ranked) if summary else ranked}


def _analysis_status(candidate):
    analysis = candidate.get("analysis")
    if analysis is None:
        return "absent"
    spans = analysis['units'] if analysis.get('version', 1) == 2 else analysis['spans']
    if not any(span["move"] != "unknown" for span in spans):
        return "abstained"
    if any(span[key] == "unknown" for span in spans for key in ("move", "polarity", "certainty")):
        return "partial"
    return "complete"


def _analysis_runs(spans):
    """Ignore arbitrary evidence subdivision, without erasing semantic changes.

    Keep the original spans in the candidate/hash. Only the feature view merges
    consecutive identical, fully known action/polarity/certainty signatures.
    Unknowns are barriers, never omitted or fused with a neighboring action.
    """
    runs = []
    for span in spans:
        signature = tuple(span[key] for key in ("move", "polarity", "certainty"))
        if runs and "unknown" not in signature and signature == runs[-1]:
            continue
        runs.append(signature)
    return runs


def _analysis_unit_trace(analysis):
    """Unit identities in source order; adjacent pieces of one unit occur once."""
    pieces = sorted((part['start'], index) for index, unit in enumerate(analysis['units'])
                    for part in unit['parts'])
    trace = []
    for _, index in pieces:
        if not trace or trace[-1] != index:
            trace.append(index)
    return trace


def _analysis_surface_runs(analysis):
    if analysis.get('version', 1) != 2:
        return _analysis_runs(analysis['spans'])
    return _analysis_runs([analysis['units'][index] for index in _analysis_unit_trace(analysis)])


def _analysis_topology_features(analysis):
    """Pair traces describe surface organization, never logical entailment."""
    if analysis is None or analysis.get('version', 1) != 2:
        return []
    units = analysis['units']
    trace = _analysis_unit_trace(analysis)
    order = list(dict.fromkeys(trace))
    features = set()
    for position, left in enumerate(order):
        for right in order[position + 1:]:
            a, b = units[left], units[right]
            if a['move'] == 'unknown' or b['move'] == 'unknown':
                continue
            pair = []
            for index in trace:
                if index in (left, right):
                    marker = 'A' if index == left else 'B'
                    if not pair or pair[-1] != marker:
                        pair.append(marker)
            pattern = ''.join(pair)
            features.add(f"pair:{a['move']}>{b['move']}:{pattern}")
            signatures = [tuple(unit[key] for key in ('move', 'polarity', 'certainty')) for unit in (a, b)]
            if all('unknown' not in signature for signature in signatures):
                features.add(f"pair_signature:{'/'.join(signatures[0])}>{'/'.join(signatures[1])}:{pattern}")
    return sorted(ANALYSIS_TOPOLOGY_PREFIX + feature for feature in features)


def _analysis_topology_capacity(analysis):
    if analysis is None or analysis.get('version', 1) != 2:
        return 0
    units = analysis['units']
    if any(unit[key] == 'unknown' for unit in units for key in ('move', 'polarity', 'certainty')):
        return len(units) * (len(units) - 1)  # two keys per unordered unit pair
    return len(_analysis_topology_features(analysis))


def _analysis_position_features(signature, position):
    move, polarity, certainty = signature
    if move == 'unknown':
        return set()
    features = {f"move_pos:{move}@{position}"}
    if polarity != 'unknown' and certainty != 'unknown':
        features.add(f"signature_pos:{move}/{polarity}/{certainty}@{position}")
    return features


def _analysis_run_features(runs):
    """The original normalized surface-order view, without a namespace."""
    features = set()
    for i, (move, polarity, certainty) in enumerate(runs):
        position = ("only" if len(runs) == 1 else "first" if i == 0
                    else "last" if i == len(runs) - 1 else "middle")
        features.update(_analysis_position_features((move, polarity, certainty), position))
        if i and move != 'unknown' and runs[i - 1][0] != "unknown":
            features.add(f"adjacent:{runs[i - 1][0]}>{move}")
    if len(runs) > 1 and all(run[0] != "unknown" for run in runs):
        features.add("move_seq:" + ">".join(run[0] for run in runs))
    return features


def _analysis_stable_features(runs):
    """Keep features certain under every compatible merging of unknown runs.

    A DAG edge is a contiguous block whose known attributes do not conflict.
    Reachability finds a partition lacking a proposed feature. This is a
    conservative overapproximation (no unknown semantic labels are guessed),
    bounded by the 16 input spans, not exponential completion enumeration.
    """
    features = _analysis_run_features(runs)
    if not any('unknown' in run for run in runs):
        return features
    count = len(runs)
    edges = [[] for _ in runs]
    for start in range(count):
        signature = ['unknown'] * 3
        for end in range(start + 1, count + 1):
            current = runs[end - 1]
            if any(a != 'unknown' and b != 'unknown' and a != b for a, b in zip(signature, current)):
                break
            signature = [b if a == 'unknown' else a for a, b in zip(signature, current)]
            position = ('only' if start == 0 and end == count else 'first' if start == 0
                        else 'last' if end == count else 'middle')
            edges[start].append((end, signature[0], _analysis_position_features(signature, position)))
    stable = set()
    for feature in features:
        if feature.startswith('move_seq:'):
            # Any compatible multi-run block changes the sequence length.
            if not any(end > start + 1 for start, options in enumerate(edges) for end, _, _ in options):
                stable.add(feature)
            continue
        reachable = [set() for _ in range(count + 1)]
        reachable[0].add(None)
        for start, options in enumerate(edges):
            for previous in reachable[start]:
                for end, move, local in options:
                    adjacent = (f'adjacent:{previous}>{move}' if previous not in (None, 'unknown') and move != 'unknown' else None)
                    if feature not in local and feature != adjacent:
                        reachable[end].add(move)
        if not reachable[count]:
            stable.add(feature)
    return stable


def _analysis_feature_capacity(analysis):
    """Retain zero-evidence slots when attributes are masked on fixed spans."""
    if analysis is None:
        return 0
    runs = _analysis_surface_runs(analysis)
    if not any('unknown' in run for run in runs):
        surface_capacity = len(_analysis_run_features(runs))
    else:
        # At most two positional keys/run, n-1 adjacencies, and one sequence.
        # Further masking can only split must-equal runs, never lower this bound.
        surface_capacity = 2 if len(runs) == 1 else 3 * len(runs)
    return surface_capacity + _analysis_topology_capacity(analysis)


def _analysis_features(analysis):
    """Extract bounded, lexical-independent features from validated spans."""
    if analysis is None:
        return []
    features = {ANALYSIS_PREFIX + feature for feature in _analysis_stable_features(_analysis_surface_runs(analysis))}
    features.update(_analysis_topology_features(analysis))
    return sorted(features)


def _surface_grammar_allowed(candidate):
    """Explicit grammar, including abstention, replaces raw character templates."""
    return "grammar" not in candidate


def _candidate_syntax_features(candidate, db, diagnostics=None, *, text_format=None):
    features, backend = _syntax_features(candidate["text"], db, diagnostics=diagnostics,
                                         **({'text_format': text_format} if text_format is not None else {}))
    surface_grammar = _surface_grammar_allowed(candidate)
    if diagnostics is not None:
        diagnostics['heuristic_template_active'] = backend in ('heuristic', 'heuristic-fallback') and surface_grammar
    if backend == "off":
        return [], backend, [], []
    analysis = candidate.get("analysis")
    # Explicit analysis (including abstention) replaces lexical academic guesses.
    base = {feature for feature in features
            if (analysis is None or ":academic:" not in feature)
            and (surface_grammar or ":heuristic_template:" not in feature)}
    annotated = _analysis_features(analysis)
    grammatical = _grammar_features(candidate.get("grammar"))
    return sorted(base | set(annotated) | set(grammatical)), backend, annotated, grammatical


def _base_syntax_comparable(backends):
    """A missing parser representation cannot establish exclusive rejection."""
    sources = set(backends)
    return len(sources) == 1 and all(backend != 'off' for backend, _ in sources)


def _choice_payload_hash(section, role, candidates, chosen_id, author_owned, *, learn_from_choice=True, edit_scope=None, text_format=None):
    """Identify a decision, excluding presentation order and runtime settings."""
    payload = {
        "version": 1,
        "section": str(section), "role": str(role),
        "candidates": sorted(candidates, key=lambda candidate: candidate["id"]),
        "chosen": chosen_id, "author_owned": author_owned,
    }
    # Absent/true is the original behavior and keeps historical hashes exact.
    # An explicit opt-out is a different immutable decision, even for the same text.
    if not learn_from_choice:
        payload["learn_from_choice"] = False
    # Scope is part of a new decision's identity; absent legacy hashes stay exact.
    if edit_scope is not None:
        payload['edit_scope'] = edit_scope
    if text_format is not None:
        payload['text_format'] = text_format
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _choice_receipt(conn, round_id, payload_hash):
    if round_id is None:
        return None
    row = conn.execute("SELECT payload_hash,result_json FROM choice_events WHERE round_id=?",
                       (round_id,)).fetchone()
    if row is None:
        return None
    if row[0] != payload_hash:
        raise ValueError("round_id already belongs to a different decision; "
                         "reuse the original payload for a retry, or use a new "
                         "round_id for a new author decision")
    return {**json.loads(row[1]), "replayed": True}


def _store_choice_receipt(conn, round_id, payload_hash, result, now):
    """Persist either learning or editorial-only completion inside its transaction."""
    if round_id is not None:
        conn.execute("INSERT INTO choice_events(round_id,payload_hash,result_json,created) "
                     "VALUES (?,?,?,?)",
                     (round_id, payload_hash, json.dumps(result, ensure_ascii=False),
                      now.isoformat()))


def _legacy_label_weights(conn, context, labels, now=None):
    result = {}
    for label in labels:
        weight, _ = _weight_of(conn, context, label, now)
        result[label] = weight
    for label, weight, last_at in conn.execute(
            "SELECT opt,weight,last_at FROM prefs WHERE context='__GLOBAL__'"):
        if label in result:
            result[label] = max(result[label], int(weight * _decay(last_at, now)))
    return result


def learning_policy(db, action=None):
    """Inspect or explicitly select the learner; old direct callers stay legacy."""
    if action is not None and action not in (*_adaptive.POLICIES, 'ensure-adaptive'):
        raise ValueError('learning policy must be legacy, adaptive-v1, adaptive-v2, adaptive-v3 or ensure-adaptive')
    with closing(_open(db)) as conn:
        if action is None:
            conn.execute('BEGIN')
            return _adaptive.policy(conn)
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            return _adaptive.configure(conn, action)


def _adaptive_vector(candidate, extracted, *, relations=False):
    """Exact nonnegative linear coefficients of scoring v2, before rounding."""
    phrase, syntax, _, analysis, grammar, _ = extracted
    base = set(syntax) - set(analysis) - set(grammar)
    vector = {'phrase|' + key: 1 / max(1, len(phrase)) for key in sorted(phrase)}
    vector.update({'syntax|' + key: SYNTAX_SCALE / max(1, len(base)) for key in sorted(base)})
    capacity = _analysis_feature_capacity(candidate.get('analysis'))
    topology = _analysis_topology_capacity(candidate.get('analysis'))
    for key in sorted(analysis):
        denominator = topology if key.startswith(ANALYSIS_TOPOLOGY_PREFIX) else capacity - topology
        vector['syntax|' + key] = SYNTAX_SCALE / max(1, denominator)
    for key in sorted(grammar):
        vector['syntax|' + key] = SYNTAX_SCALE * sum(_grammar_family_scores(
            grammar, [{'feature': key, 'weight': 1}], candidate.get('grammar')).values())
    if relations:
        vector.update(_relations.project(_grammar_observations, candidate,
                                         scale=SYNTAX_SCALE, backend=extracted[2])[0])
    return vector


def _adaptive_prepare(chosen, candidates, extracted, *, relations=False):
    """Reuse existing family eligibility over complete, distinct candidate texts."""
    distinct = {c['text']: c for c in candidates if c['text'] != chosen['text']}
    selected_vector = _adaptive_vector(chosen, extracted[chosen['text']], relations=relations)
    if not distinct:
        return selected_vector, []
    compared = [chosen, *distinct.values()]
    base_ok = _base_syntax_comparable(
        (extracted[c['text']][2], extracted[c['text']][5].get('model_fingerprint')) for c in compared)
    analysis_ok = all(extracted[c['text']][2] != 'off' and _analysis_status(c) == 'complete' for c in compared)
    topology_ok = analysis_ok and all(c['analysis']['version'] == 2 for c in compared)
    grammar_ok = all(extracted[c['text']][2] != 'off' and _grammar_status(c) == 'complete' for c in compared)
    shape_ok = grammar_ok and _argument_shapes_comparable(compared)
    adjunct_ok = (all(extracted[c['text']][2] != 'off' for c in compared)
                  and _adjunct_positions_comparable(compared))
    pair_ok = (grammar_ok and len({c['grammar']['version'] for c in compared}) == 1
               and all(x[2] is not None for c in compared for x in _grammar_link_identities(c['grammar']))
               and all(x is not None for c in compared for x in _grammar_link_pair_evidence(c['grammar'])))
    academic_ok = all('analysis' not in c for c in compared)
    template_ok = all(_surface_grammar_allowed(c) for c in compared)
    relations_ok = relations and all(extracted[c['text']][2] != 'off' and _relations.state(
        _grammar_observations, c, c.get('relation_coverage'))['fully_known'] for c in compared)

    def eligible(key):
        if key.startswith(_relations.PREFIX):
            return relations_ok
        if key.startswith('phrase|'):
            return True
        feature = key[len('syntax|'):]
        if feature.startswith((ANALYSIS_PREFIX, ANALYSIS_TOPOLOGY_PREFIX)):
            return analysis_ok and (topology_ok or not feature.startswith(ANALYSIS_TOPOLOGY_PREFIX))
        if feature.startswith(GRAMMAR_PREFIX):
            return _grammar_feature_comparable(feature, grammar_ok, shape_ok, adjunct_ok, pair_ok)
        return (base_ok and (academic_ok or ':academic:' not in feature)
                and (template_ok or ':heuristic_template:' not in feature))

    differences = {}
    for other in distinct.values():
        other_vector = _adaptive_vector(other, extracted[other['text']], relations=relations)
        difference = {key: selected_vector.get(key, 0) - other_vector.get(key, 0)
                      for key in sorted(selected_vector.keys() | other_vector.keys())
                      if eligible(key) and abs(selected_vector.get(key, 0) - other_vector.get(key, 0)) > 1e-15}
        if difference:
            differences[tuple(difference.items())] = difference
    return selected_vector, [differences[key] for key in sorted(differences)]


def _adaptive_feature_family(key):
    if key.startswith('phrase|'):
        return 'phrase'
    if key.startswith(('syntax|' + GRAMMAR_PREFIX, _relations.PREFIX)):
        return 'grammar'
    if key.startswith(('syntax|' + ANALYSIS_PREFIX, 'syntax|' + ANALYSIS_TOPOLOGY_PREFIX)):
        return 'analysis'
    return 'base'


def rank_candidates(db, section, role, candidates, *, text_format=None):
    """Rank candidate texts with phrase and optional syntax features.

    The returned order is deterministic. Phrase features and a softly scaled
    syntax signal form the score. Old Light/Medium/Bold weights break only
    equal-score ties; accumulated label history cannot overrule learned text.
    """
    if text_format is not None:
        _validated_text_format(text_format)
    format_options = {'text_format': text_format} if text_format is not None else {}
    candidates = _validated_candidates(candidates)
    with closing(_open(db)) as conn:
        context = _context(section, role)
        _require_resolved_context(conn, context)
        # Model parsing can be slow: finish it before opening the read snapshot.
        # Keep each candidate's own annotations and diagnostics, even for equal text.
        extracted = []
        for candidate in candidates:
            features = _phrase_features(candidate["text"], **format_options)
            syntax_diagnostics = {}
            syntax_features, syntax_backend, annotated, grammatical = _candidate_syntax_features(candidate, db, syntax_diagnostics, **format_options)
            extracted.append((features, syntax_features, syntax_backend, annotated, grammatical, syntax_diagnostics))
        # All label, phrase and syntax weights in this ranking share one SQLite
        # snapshot and one decay instant. Closing also releases it on exceptions.
        conn.execute("BEGIN")
        _require_resolved_context(conn, context)
        now = _parse_time(_now_iso())
        policy = _adaptive.policy(conn)
        adaptive_weights = (_adaptive.weights_at(conn, context, now, _decay)
                            if policy['policy'] in _adaptive.SCORING_VERSIONS else None)
        labels = [candidate["label"] for candidate in candidates]
        label_weights = _legacy_label_weights(conn, context, labels, now)
        ranked = []
        for candidate, extracted_item in zip(candidates, extracted):
            features, syntax_features, syntax_backend, annotated, grammatical, syntax_diagnostics = extracted_item
            matched = []
            total = 0
            for feature in features:
                weight, _ = _phrase_weight_of(conn, context, feature, now)
                if weight:
                    matched.append({"feature": feature, "weight": weight})
                    total += weight
            phrase_score = total / max(1, len(features))
            matched_syntax = []
            syntax_total = 0
            for feature in syntax_features:
                weight, _ = _syntax_weight_of(conn, context, feature, now)
                if weight:
                    matched_syntax.append({"feature": feature, "weight": weight})
                    syntax_total += weight
            grammar_matched = [item for item in matched_syntax if item["feature"] in grammatical]
            analysis_matched = [item for item in matched_syntax if item["feature"] in annotated]
            analysis_total = sum(item["weight"] for item in analysis_matched)
            grammar_by_family = _grammar_family_scores(grammatical, grammar_matched, candidate.get('grammar'))
            grammar_score = sum(grammar_by_family.values())
            analysis_capacity = (_analysis_feature_capacity(candidate.get('analysis'))
                                 if syntax_backend != 'off' else 0)
            topology_capacity = (_analysis_topology_capacity(candidate.get('analysis'))
                                 if syntax_backend != 'off' else 0)
            topology_matched = [item for item in matched_syntax if item['feature'].startswith(ANALYSIS_TOPOLOGY_PREFIX)]
            topology_total = sum(item['weight'] for item in topology_matched)
            base_count = len(syntax_features) - len(grammatical) - len(annotated)
            # A parser's unrelated/untrained keys must not dilute identical
            # rhetorical evidence across otherwise comparable candidates.
            base_syntax_score = (syntax_total - sum(item["weight"] for item in grammar_matched) - analysis_total) / max(1, base_count)
            analysis_score = (analysis_total - topology_total) / max(1, analysis_capacity - topology_capacity)
            topology_score = topology_total / max(1, topology_capacity)
            syntax_score = base_syntax_score + analysis_score + grammar_score + topology_score
            label_weight = label_weights[candidate["label"]]
            score = phrase_score + SYNTAX_SCALE * syntax_score
            ranked.append({
                **candidate,
                "score": round(score, 6),
                "scoring_version": SCORING_VERSION,
                "phrase_score": round(phrase_score, 6),
                "phrase_feature_count": len(features),
                "syntax_score": round(syntax_score, 6),
                "base_syntax_score": round(base_syntax_score, 6),
                "analysis_score": round(analysis_score, 6),
                "syntax_backend": syntax_backend,
                "heuristic_template_active": syntax_diagnostics['heuristic_template_active'],
                "syntax_requested_backend": syntax_diagnostics.get('requested_backend') or _syntax_backend_of(db),
                "syntax_fallback_reason": syntax_diagnostics.get('fallback_reason'),
                "syntax_model_version": (syntax_diagnostics.get('model_version')
                                          if syntax_backend == "stanza" else None),
                "syntax_model_fingerprint": (syntax_diagnostics.get('model_fingerprint')
                                              if syntax_backend == "stanza" else None),
                "label_weight": label_weight,
                "matched_features": matched,
                "matched_syntax_features": matched_syntax,
                "analysis_source": candidate.get("analysis", {}).get("source"),
                "analysis_input_version": candidate.get('analysis', {}).get('version'),
                "analysis_feature_version": (ANALYSIS_FEATURE_VERSION
                                             if "analysis" in candidate and syntax_backend != "off" else None),
                "analysis_status": ("disabled" if syntax_backend == "off" else _analysis_status(candidate)),
                "analysis_feature_capacity": analysis_capacity,
                "analysis_topology_score": round(topology_score, 6),
                "analysis_topology_capacity": topology_capacity,
                "analysis_topology_feature_version": (ANALYSIS_TOPOLOGY_VERSION
                                                      if candidate.get('analysis', {}).get('version') == 2
                                                      and syntax_backend != 'off' else None),
                "matched_analysis_topology_features": topology_matched,
                "matched_analysis_features": analysis_matched,
                "grammar_source": candidate.get("grammar", {}).get("source"),
                "grammar_input_version": candidate.get("grammar", {}).get("version"),
                "grammar_feature_version": (GRAMMAR_FEATURE_VERSION if "grammar" in candidate and syntax_backend != "off" else None),
                "grammar_status": ("disabled" if syntax_backend == "off" else _grammar_status(candidate)),
                "grammar_score": round(grammar_score, 6),
                "grammar_scores_by_family": {kind: round(value, 6) for kind, value in grammar_by_family.items()},
                "matched_grammar_features": grammar_matched,
            })
            if adaptive_weights is not None:
                vector = _adaptive_vector(candidate, (features, syntax_features, syntax_backend,
                                                      annotated, grammatical, syntax_diagnostics),
                                          relations=policy['policy'] == 'adaptive-v3')
                ranked[-1].update(learning_policy=policy['policy'], scoring_version=policy['scoring_version'],
                                  legacy_observation_score=ranked[-1]['score'],
                                  **_adaptive.scores(adaptive_weights, vector))
                if policy['policy'] == 'adaptive-v3':
                    relation_vector, relation_state = _relations.project(
                        _grammar_observations, candidate, scale=SYNTAX_SCALE, backend=syntax_backend)
                    ranked[-1].update(relation_score=_adaptive.scores(adaptive_weights, relation_vector)['score'],
                                      relation_feature_count=len(relation_vector),
                                      relation_coverage_status=('disabled' if syntax_backend == 'off' else
                                          'absent' if 'relation_coverage' not in candidate else
                                          'complete' if relation_state['fully_known'] else 'partial'))
    input_order = {candidate["id"]: index for index, candidate in enumerate(candidates)}
    ranked.sort(key=lambda item: (-item["score"], -item["label_weight"], input_order[item["id"]]))
    return ranked


def _rank_summary(ranked):
    """Project an already computed rank without repeating saved annotations.

    Keep text, order and every other diagnostic unchanged. This is a transport
    view, not candidate input or another scoring/validation path.
    """
    matches = {
        'phrase': 'matched_features', 'syntax': 'matched_syntax_features',
        'analysis': 'matched_analysis_features', 'grammar': 'matched_grammar_features',
        'analysis_topology': 'matched_analysis_topology_features',
    }
    omitted = {'analysis', 'grammar', 'relation_coverage', *matches.values()}
    return [{**{key: value for key, value in row.items() if key not in omitted},
             'summary_version': 1,
             'omitted_fields': sorted(omitted.intersection(row)),
             'matched_feature_counts': {name: len(row.get(key, [])) for name, key in matches.items()}}
            for row in ranked]


def _argument_shapes_comparable(candidates):
    """Older annotations cannot express a recipient; absence is not rejection."""
    grammars = [candidate.get('grammar', {}) for candidate in candidates]
    has_recipient = any(slot['role'] == 'recipient'
                        for grammar in grammars for frame in grammar.get('frames', [])
                        for slot in frame['slots'])
    return not has_recipient or all(grammar.get('version', 0) >= 5 for grammar in grammars)


def _is_argument_shape(feature):
    return feature.startswith((GRAMMAR_PREFIX + 'expression_slots:', GRAMMAR_PREFIX + 'expression:'))


def choice_file(db, section, role, path, *, expected_round_id=None):
    """Finalize a saved decision, learning unless the author explicitly opted out."""
    data, candidates = _candidate_data(path)
    return _choice_data(db, section, role, data, candidates, expected_round_id=expected_round_id)


def _choice_data(db, section, role, data, candidates, *, expected_round_id=None, receipt_path=None):
    """Consume one validated snapshot for both ordinary and exported feedback."""
    format_options = {'text_format': data['text_format']} if 'text_format' in data else {}
    _require_candidate_context(data, section, role)
    if expected_round_id is not None:
        _require_choice_round_id(data, expected_round_id)
    if data.get("chosen") is None:
        raise ValueError("choice file must contain chosen candidate id")
    chosen_id = str(data.get("chosen", ""))
    chosen = next(candidate for candidate in candidates if candidate["id"] == chosen_id)
    context = _context(section, role)
    author_owned = bool(data.get("author_owned", False))
    round_id = data.get("round_id")
    learn_from_choice = data.get("learn_from_choice", True)
    if not learn_from_choice and round_id is None:
        raise ValueError("a non-learning choice requires a saved round_id")
    payload_hash = _choice_payload_hash(section, role, candidates, chosen_id, author_owned,
                                       learn_from_choice=learn_from_choice, edit_scope=data.get('edit_scope'), **format_options)
    if receipt_path is not None:
        if round_id is None:
            raise ValueError('receipt export requires a saved round_id')
        _preflight_receipt_export(db, receipt_path, round_id, payload_hash)
    with closing(_open(db)) as conn:
        receipt = _choice_receipt(conn, round_id, payload_hash)
        if receipt is not None:
            return receipt
        if not learn_from_choice:
            result = {
                "chosen": chosen_id, "label": chosen["label"],
                "round_id": round_id, "idempotent": True, "replayed": False,
                "feedback_applied": False, "author_exemplar": False,
                "learning_context": {"section": str(section), "role": str(role)},
            }
            result.update(format_options)
            if 'edit_scope' in data:
                result['edit_scope'] = data['edit_scope']
            # Persist the editorial decision without running feature/model work.
            # Recheck under the write lock just as for normal learning below.
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                receipt = _choice_receipt(conn, round_id, payload_hash)
                if receipt is not None:
                    return receipt
                now = _parse_time(_now_iso())
                _store_choice_receipt(conn, round_id, payload_hash, result, now)
            return result
        _require_resolved_context(conn, context)
        # A replay never reparses or refreshes old feedback. For new decisions,
        # extract each distinct text once without holding a database write lock.
        extracted = {}
        for candidate in candidates:
            text = candidate["text"]
            if text not in extracted:
                diagnostics = {}
                syntax, backend, annotated, grammatical = _candidate_syntax_features(candidate, db, diagnostics, **format_options)
                extracted[text] = (set(_phrase_features(text, **format_options)), set(syntax), backend, set(annotated), set(grammatical), diagnostics)
        adaptive_vector, adaptive_differences = _adaptive_prepare(chosen, candidates, extracted)
        relative_vector, relative_differences = _adaptive_prepare(chosen, candidates, extracted, relations=True)
        chosen_phrases, chosen_syntax_features, syntax_backend, chosen_analysis, chosen_grammar, chosen_diagnostics = extracted[chosen["text"]]
        alternative_phrases, alternative_syntax, alternative_analysis = set(), set(), set()
        alternative_grammar = set()
        base_syntax_negative_feedback = _base_syntax_comparable(
            (item[2], item[5].get('model_fingerprint')) for item in extracted.values())
        analysis_negative_feedback = syntax_backend != "off" and all(
            _analysis_status(candidate) == "complete" for candidate in candidates)
        analysis_topology_negative_feedback = analysis_negative_feedback and all(
            candidate['analysis']['version'] == 2 for candidate in candidates)
        grammar_negative_feedback = syntax_backend != "off" and all(
            _grammar_status(candidate) == "complete" for candidate in candidates)
        argument_shape_negative_feedback = grammar_negative_feedback and _argument_shapes_comparable(candidates)
        adjunct_position_negative_feedback = (all(item[2] != 'off' for item in extracted.values())
                                               and _adjunct_positions_comparable(candidates))
        link_pair_negative_feedback = (grammar_negative_feedback
            and len({candidate['grammar']['version'] for candidate in candidates}) == 1
            and all(identity[2] is not None for candidate in candidates
                    for identity in _grammar_link_identities(candidate['grammar']))
            and all(key is not None for candidate in candidates
                    for key in _grammar_link_pair_evidence(candidate['grammar'])))
        for candidate in candidates:
            if candidate["id"] != chosen_id:
                phrases, syntax, _, annotated, grammatical, _ = extracted[candidate["text"]]
                alternative_phrases.update(phrases)
                base = syntax - annotated - grammatical
                if "analysis" in chosen:
                    # The chosen candidate deliberately omitted lexical academic
                    # extraction; that missing source is not negative evidence.
                    base = {feature for feature in base if ":academic:" not in feature}
                if not _surface_grammar_allowed(chosen):
                    # An explicitly replaced source cannot establish rejection
                    # of raw templates present only on a legacy alternative.
                    base = {feature for feature in base if ":heuristic_template:" not in feature}
                if base_syntax_negative_feedback:
                    alternative_syntax.update(base)
                if analysis_negative_feedback:
                    alternative_analysis.update(feature for feature in annotated
                                                if analysis_topology_negative_feedback
                                                or not feature.startswith(ANALYSIS_TOPOLOGY_PREFIX))
                alternative_grammar.update(feature for feature in grammatical
                                           if _grammar_feature_comparable(feature, grammar_negative_feedback,
                                               argument_shape_negative_feedback, adjunct_position_negative_feedback,
                                               link_pair_negative_feedback))
        result = {
            "chosen": chosen_id,
            "label": chosen["label"],
            "feedback_applied": True,
            "learning_context": {"section": str(section), "role": str(role)},
            "features": len(chosen_phrases),
            "syntax_features": len(chosen_syntax_features),
            "syntax_backend": syntax_backend,
            "heuristic_template_active": chosen_diagnostics['heuristic_template_active'],
            "syntax_requested_backend": chosen_diagnostics.get('requested_backend') or _syntax_backend_of(db),
            "syntax_fallback_reason": chosen_diagnostics.get('fallback_reason'),
            "syntax_fallbacks": {
                candidate['id']: extracted[candidate['text']][5]['fallback_reason']
                for candidate in sorted(candidates, key=lambda item: item['id'])
                if extracted[candidate['text']][5].get('fallback_reason')
            },
            "base_syntax_negative_feedback": base_syntax_negative_feedback,
            "syntax_model_version": (chosen_diagnostics.get('model_version')
                                      if syntax_backend == "stanza" else None),
            "syntax_model_fingerprint": (chosen_diagnostics.get('model_fingerprint')
                                          if syntax_backend == "stanza" else None),
            "syntax_model_fingerprints": {
                candidate['id']: extracted[candidate['text']][5].get('model_fingerprint')
                for candidate in sorted(candidates, key=lambda item: item['id'])
            },
            "author_exemplar": author_owned,
            "round_id": round_id,
            "idempotent": round_id is not None,
            "replayed": False,
            "analysis_features": len(chosen_analysis),
            "analysis_input_version": chosen.get('analysis', {}).get('version'),
            "analysis_feature_capacity": (_analysis_feature_capacity(chosen.get('analysis'))
                                          if syntax_backend != 'off' else 0),
            "analysis_topology_features": sum(feature.startswith(ANALYSIS_TOPOLOGY_PREFIX)
                                              for feature in chosen_analysis),
            "analysis_topology_capacity": (_analysis_topology_capacity(chosen.get('analysis'))
                                           if syntax_backend != 'off' else 0),
            "analysis_topology_feature_version": (ANALYSIS_TOPOLOGY_VERSION
                                                  if chosen.get('analysis', {}).get('version') == 2
                                                  and syntax_backend != 'off' else None),
            "analysis_source": chosen.get("analysis", {}).get("source"),
            "analysis_feature_version": (ANALYSIS_FEATURE_VERSION
                                         if "analysis" in chosen and syntax_backend != "off" else None),
            "analysis_status": ("disabled" if syntax_backend == "off" else _analysis_status(chosen)),
            "analysis_negative_feedback": analysis_negative_feedback,
            "analysis_topology_negative_feedback": analysis_topology_negative_feedback,
            "grammar_features": len(chosen_grammar),
            "grammar_source": chosen.get("grammar", {}).get("source"),
            "grammar_input_version": chosen.get("grammar", {}).get("version"),
            "grammar_feature_version": (GRAMMAR_FEATURE_VERSION if "grammar" in chosen and syntax_backend != "off" else None),
            "grammar_status": ("disabled" if syntax_backend == "off" else _grammar_status(chosen)),
            "grammar_negative_feedback": grammar_negative_feedback,
            "argument_shape_negative_feedback": argument_shape_negative_feedback,
            "adjunct_position_negative_feedback": adjunct_position_negative_feedback,
            "link_pair_negative_feedback": link_pair_negative_feedback,
        }
        result.update(format_options)
        if 'edit_scope' in data:
            result['edit_scope'] = data['edit_scope']
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            # Another process may have committed while this caller extracted.
            receipt = _choice_receipt(conn, round_id, payload_hash)
            if receipt is not None:
                return receipt
            _require_resolved_context(conn, context)
            # Extraction can finish out of request order. Decay and timestamp
            # the new feedback at this serialized write, not at request start.
            now = _parse_time(_now_iso())
            committed_policy = _adaptive.policy(conn)['policy']
            if committed_policy in _adaptive.SCORING_VERSIONS:
                if committed_policy == 'adaptive-v3':
                    adaptive_vector, adaptive_differences = relative_vector, relative_differences
                result['adaptive'] = _adaptive.update(
                    conn, context, adaptive_vector, adaptive_differences, author_owned,
                    now, _decay, _adaptive_feature_family, CEILING)
            _round_feature_feedback_tx(
                conn, context, chosen_phrases, alternative_phrases, _phrase_bump_tx,
                PHRASE_CHOICE_WEIGHT, PHRASE_REJECT_WEIGHT, now,
            )
            _round_feature_feedback_tx(
                conn, context, chosen_syntax_features - chosen_analysis - chosen_grammar, alternative_syntax, _syntax_bump_tx,
                SYNTAX_CHOICE_WEIGHT + (SYNTAX_AUTHOR_BONUS if author_owned else 0),
                SYNTAX_REJECT_WEIGHT, now,
            )
            _round_feature_feedback_tx(
                conn, context, chosen_analysis, alternative_analysis, _syntax_bump_tx,
                SYNTAX_CHOICE_WEIGHT + (SYNTAX_AUTHOR_BONUS if author_owned else 0),
                SYNTAX_REJECT_WEIGHT, now,
            )
            _round_feature_feedback_tx(
                conn, context, chosen_grammar, alternative_grammar, _syntax_bump_tx,
                SYNTAX_CHOICE_WEIGHT + (SYNTAX_AUTHOR_BONUS if author_owned else 0),
                SYNTAX_REJECT_WEIGHT, now,
            )
            # Preserve old callers' edit-intensity history as an auxiliary prior.
            _bump_tx(conn, context, chosen["label"], PHRASE_CHOICE_WEIGHT, 1, now)
            if author_owned:
                _store_exemplar(conn, KIND_AUTHOR, chosen["text"], now=now, **format_options)
            _store_choice_receipt(conn, round_id, payload_hash, result, now)
        return result


def _receipt_export_path(db, candidate_path, output_path):
    """Reject invalid destinations and reserved state paths before feedback."""
    try:
        output_path = os.fspath(output_path)
    except TypeError as error:
        raise ValueError('receipt file path must be a non-empty string or path') from error
    if not isinstance(output_path, str) or not output_path:
        raise ValueError('receipt file path must be a non-empty string or path')
    output_path = os.path.abspath(os.path.expanduser(output_path))
    if os.path.islink(output_path):
        raise ValueError('receipt file must not be a symbolic link')
    output_path = os.path.realpath(output_path)
    if not os.path.isdir(os.path.dirname(output_path)):
        raise ValueError('receipt file parent directory must already exist')
    if os.path.lexists(output_path) and not os.path.isfile(output_path):
        raise ValueError('receipt output must be a regular file')
    database = os.path.abspath(os.fspath(db))
    reserved = {os.path.realpath(candidate_path)}
    # SQLite resolves database aliases; engine controls use the caller's DB directory.
    for database_path in {database, os.path.realpath(database)}:
        reserved.update(os.path.realpath(database_path + suffix)
                        for suffix in ('', '-wal', '-shm', '-journal'))
        reserved.update(os.path.realpath(os.path.join(os.path.dirname(database_path), name))
                        for name in (BACKEND_FILE, SYNTAX_BACKEND_FILE,
                                     SYNTAX_MODEL_FILE, SYNTAX_MODEL_VERSION_FILE))
    if output_path in reserved:
        raise ValueError('receipt output must not occupy a candidate, database, SQLite sidecar or engine control path')
    return output_path


def _read_receipt_file(path, *, sync_content=None):
    """Read only a regular file, including when another publisher won the race."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(f'cannot read receipt file: {error}') from error
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('receipt output must be a regular file')
        content = stream.read()
        if sync_content is not None:
            if content != sync_content:
                raise ValueError('receipt output already exists with different content; preserve it and use a new receipt path')
            os.fsync(stream.fileno())
        return content


def _receipt_export_record(db, round_id, payload_hash=None):
    """Read the authoritative original receipt without creating or migrating a store."""
    database = Path(db).absolute()
    if not database.exists():
        return None
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('BEGIN')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='choice_events'").fetchone() is None:
            return None
        if payload_hash is not None:
            _choice_receipt(conn, round_id, payload_hash)
        row = conn.execute('SELECT payload_hash,result_json,created FROM choice_events WHERE round_id=?',
                           (round_id,)).fetchone()
        if row is None:
            return None
        return {'receipt_export_version': RECEIPT_EXPORT_VERSION, 'round_id': round_id,
                'payload_hash': row[0], 'created': row[2], 'result': json.loads(row[1])}


def _receipt_export_bytes(envelope):
    return (json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def _preflight_receipt_export(db, output_path, round_id, payload_hash):
    existing = _read_receipt_file(output_path)
    if existing is None:
        return
    envelope = _receipt_export_record(db, round_id, payload_hash)
    if envelope is None or existing != _receipt_export_bytes(envelope):
        raise ValueError('receipt output conflicts with the original database receipt; preserve the file and use a new receipt path')


def _sync_receipt_directory(output_path):
    descriptor = os.open(os.path.dirname(output_path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_receipt_file(content, output_path):
    """Publish complete fsynced bytes without replacement, then sync the directory."""
    existing = _read_receipt_file(output_path, sync_content=content)
    if existing is not None:
        # A prior attempt may have linked the file but failed directory fsync.
        _sync_receipt_directory(output_path)
        return True
    temporary = None
    reused = False
    try:
        with tempfile.NamedTemporaryFile(dir=os.path.dirname(output_path), prefix='.sonder-receipt-', delete=False) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, output_path)
        except FileExistsError:
            if _read_receipt_file(output_path, sync_content=content) != content:
                raise ValueError('receipt output already exists with different content; preserve it and use a new receipt path') from None
            reused = True
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    _sync_receipt_directory(output_path)
    return reused


def choice_file_export(db, section, role, path, receipt_file, *, expected_round_id=None):
    """Finalize/replay one snapshot, then export its original committed DB receipt.

    Database feedback and file publication are separate commits. A later error
    never means the feedback was rolled back; retry the same saved decision.
    """
    data, candidates = _candidate_data(path)
    output_path = _receipt_export_path(db, path, receipt_file)
    result = _choice_data(db, section, role, data, candidates,
                          expected_round_id=expected_round_id, receipt_path=output_path)
    try:
        # Verify the committed row against this same snapshot, never a reread of
        # the candidate file or the process's replay-projected result alone.
        payload_hash = _choice_payload_hash(
            section, role, candidates, str(data['chosen']), bool(data.get('author_owned', False)),
            learn_from_choice=data.get('learn_from_choice', True), edit_scope=data.get('edit_scope'),
            **({'text_format': data['text_format']} if 'text_format' in data else {}))
        envelope = _receipt_export_record(db, data['round_id'], payload_hash)
        if envelope is None:
            raise ValueError('committed receipt is missing from the database')
        if _receipt_export_bytes({**envelope['result'], 'replayed': result['replayed']}) != _receipt_export_bytes(result):
            raise ValueError('committed receipt result differs from the completed choice')
        reused = _publish_receipt_file(_receipt_export_bytes(envelope), output_path)
    except (OSError, ValueError, sqlite3.Error) as error:
        raise ValueError('receipt export failed; feedback may already be recorded. '
                         'Retry the original candidate file and round_id; preserve any conflicting receipt file. '
                         f'Detail: {error}') from error
    return {**result, 'receipt_file': output_path, 'receipt_file_reused': reused}


# --- layer 2: exemplar (sentence) memory -----------------------------------
def _store_exemplar(conn, kind, text, *, now=None, text_format=None):
    surface = _exemplar_surface(text, text_format)
    if not _matcher_norm(surface):
        raise ValueError('store: sentence surface has no characters to match on')
    ngram = _ngram_json(surface)
    now = now.isoformat() if now is not None else _now_iso()
    # Re-storing the same (kind, text) is a REINFORCEMENT, not a duplicate:
    # bump rewards and refresh last_at so the sentence counts as a stronger,
    # fresher voice anchor. (Needs the UNIQUE(kind,text) constraint.)
    conn.execute(
        "INSERT INTO exemplars(kind,text,ngram,created,last_at,rewards,text_format,ngram_version) "
        "VALUES(:k,:t,:n,:c,:c,1,:f,:v) "
        "ON CONFLICT(kind,text) DO UPDATE SET "
        "  rewards=rewards+1, last_at=:c, ngram=:n, text_format=:f, ngram_version=:v",
        {"k": kind, "t": text, "n": ngram, "c": now, "f": text_format, "v": EXEMPLAR_INDEX_VERSION},
    )
    rows = conn.execute("SELECT id,last_at,rewards FROM exemplars").fetchall()
    excess = len(rows) - MAX_EXEMPLARS
    if excess > 0:
        # Always admit the just-accepted sentence; evict the least useful old
        # history using decayed reinforcement, not lifetime reward counts.
        saved_id = conn.execute("SELECT id FROM exemplars WHERE kind=? AND text=?",
                                (kind, text)).fetchone()[0]
        instant = _parse_time(now)
        candidates = sorted(
            (row for row in rows if row[0] != saved_id),
            key=lambda row: (row[2] * _decay(row[1], instant),
                             -_age_days(row[1], instant), row[0]),
        )
        conn.executemany("DELETE FROM exemplars WHERE id=?",
                         [(row[0],) for row in candidates[:excess]])


def store(db, kind, text, *, text_format=None):
    """Store an author sentence as a canonical voice exemplar.

    Refuses an empty/whitespace sentence and one that normalizes to nothing
    (e.g. pure punctuation): such a row carries no n-grams, can never match,
    and would sit in the store as permanent zero-score noise."""
    if not str(text).strip():
        raise ValueError("store: sentence is empty")
    if not _matcher_norm(_exemplar_surface(text, text_format)):
        raise ValueError("store: sentence has no letters, digits or CJK characters to match on")
    with closing(_open(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _store_exemplar(conn, kind, text, **({'text_format': text_format} if text_format is not None else {}))


def _cached_ngram_counts(value):
    counts = json.loads(value)
    if (not isinstance(counts, dict)
            or any(not isinstance(gram, str) or type(count) is not int or count <= 0
                   for gram, count in counts.items())):
        raise ValueError('invalid exemplar ngram cache')
    return counts


def nearest(db, text, n=5, min_sim=EXEMPLAR_MIN_SIM, *, text_format=None):
    """Compare format-aware surfaces while returning exact raw author sentences.

    The default ngram backend uses one versioned cache for corpus frequency and
    document vectors. Optional matchers receive those same text interpretations;
    similarity, decay, thresholds and tie order retain their existing policies.
    """
    query = _exemplar_surface(text, text_format)
    if not _matcher_norm(query):
        return []
    with closing(_open(db)) as conn:
        rows = conn.execute(
            "SELECT id,kind,text,ngram,last_at,rewards,text_format,ngram_version FROM exemplars"
        ).fetchall()
    if not rows:
        return []
    for row in rows:
        _validate_exemplar_index_version(row[7])
        if row[7] != EXEMPLAR_INDEX_VERSION:
            raise ValueError('exemplar index changed during retrieval; retry')
    surfaces = [_exemplar_surface(row[2], row[6]) for row in rows]
    matchable = [bool(_matcher_norm(surface)) for surface in surfaces]
    backend = _backend_of(db)
    sim_by_id = {}

    if backend == "embed":
        if any(matchable):
            vecs, _ = _vectors_for([query] + surfaces, backend)
            for row, vec, usable in zip(rows, vecs[1:], matchable):
                sim_by_id[row[0]] = _cosine_dense(vecs[0], vec) if usable else 0.0
    elif backend == "sklearn":
        if any(matchable):
            try:
                mat, _ = _vectors_for([query] + surfaces, backend)
            except ValueError as error:
                if 'empty vocabulary' not in str(error).lower():
                    raise
                mat = None  # All derived strings may be shorter than char(3,3).
            if mat is not None:
                for index, (row, usable) in enumerate(zip(rows, matchable), 1):
                    sim_by_id[row[0]] = _cosine_sparse(mat[0], mat[index]) if usable else 0.0
    else:
        cached = [_cached_ngram_counts(row[3]) for row in rows]
        df = Counter()
        for counts in cached:
            df.update(counts.keys())
        n_docs = len(rows)
        idf = {gram: math.log((n_docs + 1) / (count + 1)) + 1.0 for gram, count in df.items()}
        qvec = _ngram_counts_vec(_ngram_counts(query), idf)
        for row, counts in zip(rows, cached):
            dvec = _ngram_counts_vec(counts, idf)
            sim_by_id[row[0]] = sum(qvec.get(gram, 0.0) * dvec.get(gram, 0.0) for gram in qvec)

    scored = []
    for id_, kind, raw, _, last_at, rewards, mode, _ in rows:
        sim = sim_by_id.get(id_, 0.0)
        if sim < min_sim:
            continue
        scored.append({
            "id": id_, "kind": kind, "text": raw,
            "sim": round(sim, 4),
            "age_days": round(_age_days(last_at), 1),
            "decay": round(_decay(last_at), 4),
            "rewards": rewards,
            "score": round(sim * _decay(last_at), 4),
            **({'text_format': mode} if mode is not None else {}),
        })
    scored.sort(key=lambda row: row["score"], reverse=True)
    return scored[:n]


def list_exemplars(db, kind=None):
    with closing(_open(db)) as conn:
        if kind:
            rows = conn.execute("SELECT id,kind,text,last_at,rewards,text_format FROM exemplars WHERE kind=? "
                                "ORDER BY last_at DESC", (kind,)).fetchall()
        else:
            rows = conn.execute("SELECT id,kind,text,last_at,rewards,text_format FROM exemplars "
                                "ORDER BY last_at DESC").fetchall()
    return [{"id": i, "kind": k, "text": t, "last_at": la, "rewards": r,
             **({'text_format': mode} if mode is not None else {})}
            for i, k, t, la, r, mode in rows]


# --- upkeep -----------------------------------------------------------------
def prune(db, stale_days=30, min_weight=MIN_WEIGHT):
    """Drop stale preference/example entries — the memory 'forgets' the things
    the author stopped reinforcing rather than holding them forever.

    The test is STALENESS plus DECAYED WEIGHT, never a raw pick count. An entry
    is pruned when it has gone `stale_days` without being exercised AND its
    decayed weight has fallen below `min_weight`. Two consequences:

    - A fresh single confirmation always survives a paragraph pause (it is not
      stale), so a one-off "keep this" is never deleted the moment it's written.
    - A heavily-used preference the author has since abandoned DOES get removed
      once it is old enough to have decayed below the floor. (The previous
      `picks <= N` guard made any option picked more than N times immortal —
      a 3-pick preference survived 400 days of disuse. That contradicted the
      "learn to forget" promise.)

    Decay at read time already lowers a long-unused preference; this removes it
    once it's genuinely dry."""
    now = _parse_time(_now_iso())
    with closing(_open(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT context,opt,weight,last_at FROM prefs WHERE context!='__GLOBAL__'"
        ).fetchall()
        for ctx, opt, w, last in rows:
            if _age_days(last, now) > int(stale_days) and int(w * _decay(last, now)) < min_weight:
                conn.execute("DELETE FROM prefs WHERE context=? AND opt=?", (ctx, opt))
        rows = conn.execute(
            "SELECT context,feature,weight,last_at FROM phrase_prefs"
        ).fetchall()
        for ctx, feature, weight, last in rows:
            if _age_days(last, now) > int(stale_days) and int(weight * _decay(last, now)) < min_weight:
                conn.execute(
                    "DELETE FROM phrase_prefs WHERE context=? AND feature=?",
                    (ctx, feature),
                )
        rows = conn.execute(
            "SELECT context,feature,weight,last_at FROM syntax_prefs"
        ).fetchall()
        for ctx, feature, weight, last in rows:
            if _age_days(last, now) > int(stale_days) and int(weight * _decay(last, now)) < min_weight:
                conn.execute(
                    "DELETE FROM syntax_prefs WHERE context=? AND feature=?",
                    (ctx, feature),
                )
        rows = conn.execute("SELECT id,last_at,rewards FROM exemplars").fetchall()
        for id_, last, rewards in rows:
            if _age_days(last, now) > int(stale_days) and rewards * _decay(last, now) < 1:
                conn.execute("DELETE FROM exemplars WHERE id=?", (id_,))


def reshow(db):
    conn = _open(db)
    rows = conn.execute(
        "SELECT context, opt, weight, picks, last_at FROM prefs "
        "WHERE context!='__GLOBAL__' AND picks>0 ORDER BY last_at DESC"
    ).fetchall()
    conn.close()
    print(json.dumps(
        [{"context": c, "opt": o, "weight": w, "picks": p, "last_at": la}
         for c, o, w, p, la in rows],
        ensure_ascii=False, indent=2,
    ))


def get_backend(db):
    return _backend_of(db)


def set_backend(db, backend):
    return _set_backend(db, backend)


def get_syntax_backend(db):
    return _syntax_backend_of(db)


def set_syntax_backend(db, backend, model_dir=None, model_version=None):
    return _set_syntax_backend(db, backend, model_dir, model_version)


def _read_sentence(path):
    # Preserve literal Unicode, backslashes, CRLF and trailing newlines.
    with open(path, "r", encoding="utf-8", newline="") as source:
        return source.read()


def engine_info():
    """Code protocol only: no store/config reads and no optional model imports."""
    return {
        "engine": "sonder",
        "info_version": 1,
        "scoring_version": SCORING_VERSION,
        "supported_scoring_versions": [SCORING_VERSION, *_adaptive.SCORING_VERSIONS.values()],
        "learning_policies": list(_adaptive.POLICIES),
        "database_schema": SCHEMA_VERSION,
        "context_key_version": CONTEXT_KEY_VERSION,
        "exemplar_index_version": EXEMPLAR_INDEX_VERSION,
        "receipt_export_version": RECEIPT_EXPORT_VERSION,
        "capabilities": ["injective_context_keys", "coextensive_grammar_frames", "checked_candidate_preparation", "relative_preference_learning", "relation_coverage", "local_opposition_learning", "independent_adjunct_feedback", "unanchored_grammar_guard", "adaptive_preference_learning", "literal_text_files", "phrase_memory", "author_exemplars", "choice_receipts", "adjunct_position", "annotation_binding", "syntax_fallback_diagnostics", "syntax_model_identity", "choice_id_guard", "grounded_grammar_precedence", "text_first_ranking", "choice_learning_control", "choice_context_guard", "analysis_uncertainty_guard", "phrase_literal_guard", "choice_edit_scope_guard", "independent_analysis_scoring", "grammar_link_order", "grammar_link_identity", "grammar_observation_identity", "grammar_binding_capacity", "compact_ranking", "rank_summary_input_guard", "feedback_commit_time", "complete_phrase_features", "consistent_rank_snapshot", "formatting_text_continuity", "markdown_text_format", "exemplar_format", "choice_receipt_export"],
        "input_versions": {
            "analysis": list(ANALYSIS_INPUT_VERSIONS),
            "grammar": list(GRAMMAR_INPUT_VERSIONS),
            "relation_coverage": [_relations.VERSION],
        },
        "feature_versions": {
            "syntax": SYNTAX_FEATURE_VERSION,
            "analysis": ANALYSIS_FEATURE_VERSION,
            "analysis_topology": ANALYSIS_TOPOLOGY_VERSION,
            "grammar": GRAMMAR_FEATURE_VERSION,
            "relations": f"v{_relations.VERSION}",
            "syntax_model_identity": f"v{STANZA_IDENTITY_VERSION}",
        },
        "annotation_limits": {
            "grammar_frames": MAX_GRAMMAR_FRAMES,
            "analysis_parts": MAX_ANALYSIS_SPANS,
            "grammar_expression_slots": len(_grammar_slot_roles(max(GRAMMAR_INPUT_VERSIONS), 'expression')),
        },
    }


def _exemplar_cli_options(args, *, allow_limit=False):
    """Parse only arguments after the fixed literal-text/file position."""
    tail = list(args)
    limit = 5
    if allow_limit and tail and tail[0] != '--text-format':
        limit = int(tail.pop(0))
    options = {}
    if tail:
        if len(tail) != 2 or tail[0] != '--text-format':
            raise ValueError('expected optional --text-format markdown after the text/file and optional limit')
        options['text_format'] = _validated_text_format(tail[1])
    return limit, options


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    cmd = sys.argv[1]
    try:
        if cmd == "engine-info":
            if len(sys.argv) != 2:
                raise ValueError("engine-info takes no arguments or database path")
            print(json.dumps(engine_info(), ensure_ascii=False))
        elif cmd == 'bind-file':
            if len(sys.argv) != 4:
                raise ValueError('bind-file requires exactly a draft JSON path and an output JSON path')
            print(json.dumps(bind_file(sys.argv[2], sys.argv[3]), ensure_ascii=False))
        elif cmd == 'prepare-file':
            if len(sys.argv) not in (7, 8) or (len(sys.argv) == 8 and sys.argv[7] != '--summary'):
                raise ValueError('prepare-file requires DB SECTION ROLE DRAFT OUTPUT and optional --summary')
            print(json.dumps(prepare_file(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6],
                                          summary=len(sys.argv) == 8), ensure_ascii=False))
        elif cmd == "learning-policy":
            if len(sys.argv) not in (3, 4):
                raise ValueError("learning-policy requires DB and optional ACTION")
            print(json.dumps(learning_policy(sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else None), ensure_ascii=False))
        elif cmd == "record":
            # weight is optional — SKILL.md documents the 4-arg form
            # `record <db> <section> <role> <opt>`; default to 1000.
            record(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5],
                   sys.argv[6] if len(sys.argv) > 6 else 1000)
            print("ok")
        elif cmd == "signal":
            signal(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5],
                   sys.argv[6] if len(sys.argv) > 6 else -1000)
            print("ok")
        elif cmd == "global":
            global_pref(sys.argv[2], sys.argv[3],
                        sys.argv[4] if len(sys.argv) > 4 else 100_000)
            print("ok")
        elif cmd == "bias":
            sys.stdout.write(json.dumps(bias(sys.argv[2], sys.argv[3], sys.argv[4],
                                             sys.argv[5:] if len(sys.argv) > 5 else []),
                                        ensure_ascii=False))
        elif cmd in ("store", "store-file"):
            _, format_options = _exemplar_cli_options(sys.argv[5:])
            text = _read_sentence(sys.argv[4]) if cmd == "store-file" else sys.argv[4]
            store(sys.argv[2], sys.argv[3], text, **format_options)
            print("ok")
        elif cmd in ("nearest", "nearest-file"):
            n, format_options = _exemplar_cli_options(sys.argv[4:], allow_limit=True)
            text = _read_sentence(sys.argv[3]) if cmd == "nearest-file" else sys.argv[3]
            sys.stdout.write(json.dumps(nearest(sys.argv[2], text, n, **format_options),
                                        ensure_ascii=False))
        elif cmd == "rank-file":
            if len(sys.argv) not in (6, 7) or (len(sys.argv) == 7 and sys.argv[6] != '--summary'):
                raise ValueError('rank-file requires DB SECTION ROLE JSON and optional --summary')
            data, candidates = _candidate_data(sys.argv[5])
            _require_candidate_context(data, sys.argv[3], sys.argv[4])
            ranked = rank_candidates(sys.argv[2], sys.argv[3], sys.argv[4], candidates,
                                     **({'text_format': data['text_format']} if 'text_format' in data else {}))
            sys.stdout.write(json.dumps(
                _rank_summary(ranked) if len(sys.argv) == 7 else ranked,
                ensure_ascii=False,
            ))
        elif cmd == "choice-file":
            if len(sys.argv) not in (6, 8) or (len(sys.argv) == 8 and sys.argv[6] != '--receipt-file'):
                raise ValueError('choice-file requires DB SECTION ROLE JSON and optional --receipt-file FILE')
            result = (choice_file_export(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[7])
                      if len(sys.argv) == 8 else choice_file(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]))
            print(json.dumps(result, ensure_ascii=False))
        elif cmd == "list":
            k = sys.argv[3] if len(sys.argv) > 3 else None
            sys.stdout.write(json.dumps(list_exemplars(sys.argv[2], k), ensure_ascii=False))
        elif cmd == "backend":
            if len(sys.argv) > 3:
                set_backend(sys.argv[2], sys.argv[3])
            print(get_backend(sys.argv[2]))
        elif cmd == "syntax-backend":
            if len(sys.argv) > 3:
                set_syntax_backend(
                    sys.argv[2], sys.argv[3],
                    sys.argv[4] if len(sys.argv) > 4 else None,
                    sys.argv[5] if len(sys.argv) > 5 else None,
                )
            print(get_syntax_backend(sys.argv[2]))
        elif cmd == "prune":
            prune(sys.argv[2],
                  int(sys.argv[3]) if len(sys.argv) > 3 else 30,          # stale_days
                  int(sys.argv[4]) if len(sys.argv) > 4 else MIN_WEIGHT)  # min_weight
            print("ok")
        elif cmd == "reshow":
            reshow(sys.argv[2])
        else:
            raise ValueError("unknown command; see --help")
    except (IndexError, ValueError) as e:
        print("bad arguments: %s" % e, file=sys.stderr)
        sys.exit(1)
    except (OSError, sqlite3.Error) as e:
        label = 'candidate binding error' if cmd == 'bind-file' else 'learning store error'
        print(f"{label}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
