#!/usr/bin/env python3
"""
learn.py — two-layer, context-scoped preference learning for hardworking-paper-writer.

This is a direct port of the "gets to know you" mechanism used by Metasequoia IME
(user_dictionary_journal.cpp) and corroborated by libpinyin (pinyin_remember_user_input
+ pinyin_train), ZFVimIM (historical re-ranking), and Rime's user dictionary
(count `c` / decay `d` / timestamp `t`). It is extended with CBR-style exemplar
memory + age-decay, as in Rime/Letta-style hierarchical memory.

Two layers, two jobs:

  1) `prefs` — weighted choice record. Every author choice is a weight on a
     (section::role, option) tuple. Reading applies EXPONENTIAL DECAY by age, so
     a preference you stopped reinforcing slowly fades instead of growing forever.
     Nothing is ever banned; a rejected option is only gentle-downgraded.

  2) `exemplars` — sentence memory. When the author KEEPS a sentence as-is, or
     types their own wording in "Other", that exact sentence is stored as a
     canonical example of their voice. `nearest()` retrieves the most similar
     stored sentences by cosine similarity, using one of three swappable backends
     (see # Similarity backends below). By default it's a zero-dependency
     character 3-gram TF-IDF matcher; optionally it uses sklearn TF-IDF or a
     `sentence-transformers` embedding model. The rewrites are then steered toward
     the author's own clean sentences, not toward a generic editor.

The clear distinction from a "tiny neural network": this is the same mechanism
at the sparse, explicit end of the spectrum. It generalizes by token overlap
(character n-grams) instead of by learned embeddings; the two are the coarse
and fine ends of memory. Turning on the `embed` backend swaps in a pretrained
(published, not trained-on-your-text) embedding model — a quality upgrade, not a
new training step, and entirely optional.

Run via CLI. Every command talks to the same SQLite database (preference.db).
Usage:
  python3 learn.py record <db> <section> <role> <opt> <weight>
  python3 learn.py signal <db> <section> <role> <opt> [weight]
  python3 learn.py bias   <db> <section> <role> [options...]
  python3 learn.py store  <db> <kind> <sentence>            # store author sentence
  python3 learn.py nearest <db> <sentence> [n]              # similar author sentences
  python3 learn.py list   <db> [kind]                       # view stored sentences
  python3 learn.py prune  <db> [minpicks] [minweight]
  python3 learn.py reshow <db>
  python3 learn.py backend <db> [ngram|sklearn|embed]       # set/pick similarity backend
"""

import json
import math
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone

# --- Tunables ---------------------------------------------------------------
FLOOR = 1                      # lowest managed weight
CEILING = 100_000_000          # highest managed weight
HALF_LIFE_DAYS = 30.0          # decay: weight halves every 30 days
MIN_WEIGHT = 1000              # below this an option is considered stale
EXEMPLAR_MIN_SIM = 0.0         # minimum cosine similarity to be worth returning
MAX_EXEMPLARS = 500            # cap on stored sentences (per paper)

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


# --- Matchers ---------------------------------------------------------------
def _matcher_norm(text):
    """Normalized text for both TF-IDF matchers."""
    return " ".join(re.findall(r"[A-Za-z0-9]+", text.lower()))


def _ngram_vec(text, df, idf):
    """Determine character 3-gram TF-IDF unit vector. Pure Python (default)."""
    t = _matcher_norm(text)
    if not t:
        return {}
    pad = "  "
    t = pad + t + pad
    q = Counter(t[i:i + 3] for i in range(len(t) - 2) if t[i:i + 3].strip())
    if not q:
        return {}
    weighted = {g: q[g] * idf.get(g, 0.0) for g in q}
    norm = math.sqrt(sum(v * v for v in weighted.values())) or 1.0
    return {g: v / norm for g, v in weighted.items()}


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
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS prefs (
            context  TEXT NOT NULL,
            opt      TEXT NOT NULL,
            weight   INTEGER NOT NULL DEFAULT 0,
            picks    INTEGER NOT NULL DEFAULT 0,
            last_at  TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (context, opt)
        );
        CREATE TABLE IF NOT EXISTS seen (
            context TEXT NOT NULL,
            opt     TEXT NOT NULL,
            at      TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (context, opt)
        );
        CREATE TABLE IF NOT EXISTS exemplars (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            kind     TEXT NOT NULL,
            text     TEXT NOT NULL,
            ngram    TEXT NOT NULL,
            created  TEXT NOT NULL,
            last_at  TEXT NOT NULL,
            rewards  INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    return conn


def _context(section, role):
    return f"{section}::{role}"


# --- decay ------------------------------------------------------------------
def _age_days(last_at):
    try:
        last = datetime.fromisoformat(last_at)
    except (ValueError, TypeError):
        last = datetime.now(timezone.utc)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return max(0.0, (now - last).total_seconds() / 86400.0)


def _decay(last_at):
    """Multiplier in [0,1] — a preference halves every HALF_LIFE_DAYS. This is
    what makes the learning forget instead of accumulate forever."""
    return math.pow(0.5, _age_days(last_at) / HALF_LIFE_DAYS)


def _weight_of(conn, context, opt):
    r = conn.execute(
        "SELECT weight, picks, last_at FROM prefs WHERE context=? AND opt=?",
        (context, opt),
    ).fetchone()
    if not r:
        return 0, 0
    w, p, last = r
    return int(w * _decay(last)), p


# --- layer 1: weighted choices ---------------------------------------------
def _bump(conn, context, opt, delta, picks_delta=1):
    conn.execute(
        """
        INSERT INTO prefs(context, opt, weight, picks) VALUES(:ctx,:opt,:w,:p)
        ON CONFLICT(context,opt) DO UPDATE SET
          weight=MAX(:w, weight+:w2), picks=picks+:p2,
          last_at=datetime('now')
        """,
        {
            "ctx": context,
            "opt": opt,
            "w": _clamp(delta),
            "w2": delta,
            "p": picks_delta,
            "p2": picks_delta,
        },
    )


def record(db, section, role, opt, weight=1000):
    """The author picked `opt` in this context. Increase its weight/picks."""
    conn = _open(db)
    _bump(conn, _context(section, role), opt, int(weight) or 1000, 1)
    conn.commit()
    conn.close()


def signal(db, section, role, opt, weight=-1000):
    """The author declined `opt` here. Nudge it DOWN, never ban it. It can
    compete back up if the author later prefers it (this is the IME behavior)."""
    conn = _open(db)
    w, _ = _weight_of(conn, _context(section, role), opt)
    _bump(conn, _context(section, role), opt, max(int(weight) or -1000, FLOOR - w), 0)
    conn.commit()
    conn.close()


def global_pref(db, opt, weight=100_000):
    """The author explicitly said 'always / never'. The ONLY global rule, and it
    must be earned by an explicit statement — like force_top in the IME."""
    conn = _open(db)
    _bump(conn, "__GLOBAL__", opt, int(weight) or 100_000, 0)
    conn.commit()
    conn.close()


def bias(db, section, role, options):
    """Soft ranking for `options` in this context, decay applied at read time.
    The caller uses this to ORDER its proposal (lead with higher weights)
    WITHOUT dropping any option."""
    conn = _open(db)
    ctx = _context(section, role)
    out = {}
    for opt in options:
        w, p = _weight_of(conn, ctx, opt)
        out[opt] = {"weight": w, "picks": p}
    g = conn.execute("SELECT opt, weight, last_at FROM prefs WHERE context='__GLOBAL__'").fetchall()
    for opt, w, last in g:
        if opt in out:
            out[opt]["weight"] = max(out[opt]["weight"], int(w * _decay(last)))
    conn.close()
    return out


# --- layer 2: exemplar (sentence) memory -----------------------------------
def _store_exemplar(conn, kind, text):
    ngram = json.dumps(dict(Counter(
        (lambda t: [t[i:i + 3] for i in range(len(t) - 2) if t[i:i + 3].strip()])(
            _matcher_norm(text)
        )
    )), ensure_ascii=False, sort_keys=True)
    now = _now_iso()
    conn.execute(
        "INSERT OR REPLACE INTO exemplars(kind,text,ngram,created,last_at,rewards) "
        "VALUES(:k,:t,:n,:c,:c,:r)",
        {"k": kind, "t": text, "n": ngram, "c": now, "r": 1},
    )


def store(db, kind, text):
    """Store an author sentence as a canonical voice exemplar."""
    conn = _open(db)
    _store_exemplar(conn, kind, text)
    conn.commit()
    conn.close()


def nearest(db, text, n=5, min_sim=EXEMPLAR_MIN_SIM):
    """Return the `n` stored sentences most similar to `text` (decayed, so stale
    sentences count less). The similarity matcher is chosen per-workspace by the
    backend flag: `ngram` (default, zero-dependency character 3-gram TF-IDF),
    `sklearn` (scikit-learn char TF-IDF), or `embed` (pretrained sentence
    embeddings). The backend is read from `backend <db>`; the callers don't
    need to know which one is active."""
    conn = _open(db)
    rows = conn.execute(
        "SELECT id, kind, text, ngram, last_at, rewards FROM exemplars"
    ).fetchall()
    conn.close()

    if not rows:
        return []

    backend = _backend_of(db)
    texts = [t for (_, _, t, _, _, _) in rows]

    sim_by_id = {}

    if backend == "embed":
        vecs, _ = _vectors_for([text] + texts, backend)   # dense, L2-normalized
        q = vecs[0]
        for (id_, _, _, _, _, _), vec in zip(rows, vecs[1:]):
            sim_by_id[id_] = _cosine_dense(q, vec)

    elif backend == "sklearn":
        mat, _ = _vectors_for([text] + texts, backend)    # sparse char TF-IDF
        q = mat[0]
        for (id_, _, _, _, _, _), i in zip(rows, range(1, mat.shape[0])):
            sim_by_id[id_] = _cosine_sparse(q, mat[i])

    else:  # ngram — zero-dependency, run per-pair with IDF over the corpus
        df = Counter()
        for _, _, _, ng, _, _ in rows:
            for g in json.loads(ng):
                df[g] += 1
        n_docs = len(rows)
        idf = {g: math.log((n_docs + 1) / (c + 1)) + 1.0 for g, c in df.items()}
        qvec = _ngram_vec(text, df, idf)
        for id_, _, t, _, _, _ in rows:
            dvec = _ngram_vec(t, df, idf)
            sim_by_id[id_] = sum(qvec.get(g, 0.0) * dvec.get(g, 0.0) for g in qvec)

    scored = []
    for (id_, kind, t, ng, last_at, rewards) in rows:
        sim = sim_by_id.get(id_, 0.0)
        if sim < min_sim:
            continue
        scored.append({
            "id": id_,
            "kind": kind,
            "text": t,
            "sim": round(sim, 4),
            "age_days": round(_age_days(last_at), 1),
            "decay": round(_decay(last_at), 4),
            "rewards": rewards,
            "score": round(sim * _decay(last_at), 4),   # decay applied to rank
        })
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:n]


def list_exemplars(db, kind=None):
    conn = _open(db)
    if kind:
        rows = conn.execute("SELECT id,kind,text,last_at,rewards FROM exemplars WHERE kind=? "
                            "ORDER BY last_at DESC", (kind,)).fetchall()
    else:
        rows = conn.execute("SELECT id,kind,text,last_at,rewards FROM exemplars "
                            "ORDER BY last_at DESC").fetchall()
    conn.close()
    return [{"id": i, "kind": k, "text": t, "last_at": la, "rewards": r}
            for i, k, t, la, r in rows]


# --- upkeep -----------------------------------------------------------------
def prune(db, min_picks=0, min_weight=MIN_WEIGHT, stale_days=30):
    """Drop stale preference/example entries — the memory 'forgets' the things
    the author stopped reinforcing rather than holding them forever.

    The primary test is STALENESS, not pick count: an entry is pruned only if it
    has gone `stale_days` without being exercised (or was never exercised and is
    now old). This keeps a fresh, single confirmation alive — a one-off 'keep
    this' should survive a paragraph pause, not be deleted the moment it's written.
    The decay at read time already lowers a long-unused preference; this removes it
    once it's genuinely dry."""
    conn = _open(db)
    # Purge preferences that have been idle past stale_days and have decayed low.
    conn.execute(
        "DELETE FROM prefs WHERE context!='__GLOBAL__' AND "
        "((last_at IS NULL OR last_at < datetime('now', ?) ) AND picks <= ?)",
        (f"-{int(stale_days)} days", min_picks),
    )
    # Drop exemplars that are old and were essentially never reinforced.
    conn.execute(
        "DELETE FROM exemplars WHERE rewards< ? AND "
        "last_at < datetime('now', ?)",
        (2, f"-{int(stale_days)} days"),
    )
    conn.commit()
    conn.close()


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


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    try:
        if cmd == "record":
            record(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6])
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
        elif cmd == "store":
            store(sys.argv[2], sys.argv[3], sys.argv[4])
            print("ok")
        elif cmd == "nearest":
            n = int(sys.argv[4]) if len(sys.argv) > 4 else 5
            sys.stdout.write(json.dumps(nearest(sys.argv[2], sys.argv[3], n),
                                        ensure_ascii=False))
        elif cmd == "list":
            k = sys.argv[3] if len(sys.argv) > 3 else None
            sys.stdout.write(json.dumps(list_exemplars(sys.argv[2], k), ensure_ascii=False))
        elif cmd == "backend":
            if len(sys.argv) > 3:
                set_backend(sys.argv[2], sys.argv[3])
            print(get_backend(sys.argv[2]))
        elif cmd == "prune":
            prune(sys.argv[2],
                  int(sys.argv[3]) if len(sys.argv) > 3 else 2,
                  int(sys.argv[4]) if len(sys.argv) > 4 else MIN_WEIGHT)
            print("ok")
        elif cmd == "reshow":
            reshow(sys.argv[2])
        else:
            print("unknown command; see --help", file=sys.stderr)
    except (IndexError, ValueError) as e:
        print("bad arguments: %s" % e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
