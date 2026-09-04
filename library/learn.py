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
     stored sentences by character 3-gram TF-IDF cosine — a pure-Python vector
     matcher, no training, no heavy deps. The rewrites are then steered toward
     the author's own clean sentences, not toward a generic editor.

The clear distinction from a "tiny neural network": this is the same mechanism
at the sparse, explicit end of the spectrum. It generalizes by token overlap
(character n-grams) instead of by learned embeddings; the two are the coarse
and fine ends of memory. This layer is deliberately replaceable — a caller can
swap `charsim` (below) for a sentence-transformers model without changing the API.

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
_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _normalize(text):
    """Lowercase, fold whitespace, keep letters/digits/spaces. Enough to make
    character 3-grams meaningful across 'We measured' vs 'we measure'."""
    return " ".join(_WORD_RE.findall(text.lower()))


def _ngrams(text, n=3):
    """Character n-grams of a normalized string, padded at both edges."""
    t = _normalize(text)
    if len(t) < 1:
        return Counter()
    pad = " " * (n - 1)
    t = pad + t + pad
    return Counter(t[i:i + n] for i in range(len(t) - n + 1) if t[i:i + n].strip())


def _tfidf_cosine(text, vocab_df, vocab_idf):
    q = _ngrams(text)
    if not q:
        return {}
    weighted = {g: q[g] * vocab_idf.get(g, 0.0) for g in q}
    norm = math.sqrt(sum(v * v for v in weighted.values())) or 1.0
    return {g: v / norm for g, v in weighted.items()}


def _store_exemplar(conn, kind, text):
    ngram = json.dumps(dict(_ngrams(text)), ensure_ascii=False, sort_keys=True)
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
    sentences count less). Built as pure-Python character 3-gram TF-IDF cosine —
    no training, no external deps. Swap in a sentence-transformers model here
    without changing the callers."""
    conn = _open(db)
    rows = conn.execute(
        "SELECT id, kind, text, ngram, last_at, rewards FROM exemplars"
    ).fetchall()
    conn.close()

    if not rows:
        return []

    # IDF over the corpus.
    df = Counter()
    for _, _, _, ng, _, _ in rows:
        for g in json.loads(ng):
            df[g] += 1
    n_docs = len(rows)
    idf = {g: math.log((n_docs + 1) / (c + 1)) + 1.0 for g, c in df.items()}

    qvec = _tfidf_cosine(text, df, idf)
    scored = []
    for id_, kind, t, ng, last_at, rewards in rows:
        dvec = _tfidf_cosine(t, df, idf)
        # Dot product of unit vectors = cosine.
        sim = sum(qvec.get(g, 0.0) * dvec.get(g, 0.0) for g in qvec)
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
def prune(db, min_picks=2, min_weight=MIN_WEIGHT):
    """Drop stale preference/example entries — the memory 'forgets' the things
    the author stopped reinforcing rather than holding them forever. This pairs
    with the decay applied at read time."""
    conn = _open(db)
    # Purge prefs whose decayed weight has fallen below the floor. Only drop an
    # option that the author never really used (low picks) AND lost its weight
    # (low decayed weight). An option the author used but has since been nudged
    # down stays available — a downweight is not a deletion.
    rows = conn.execute("SELECT context, opt, weight, picks, last_at FROM prefs "
                        "WHERE context!='__GLOBAL__'").fetchall()
    for ctx, opt, w, p, last in rows:
        if p < min_picks and int(w * _decay(last)) < min_weight:
            conn.execute("DELETE FROM prefs WHERE context=? AND opt=?", (ctx, opt))
    # Drop exemplars that have been re-encoded as stale (few rewards, old).
    conn.execute(
        "DELETE FROM exemplars WHERE rewards<2 AND last_at<datetime('now','-30 days')"
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
