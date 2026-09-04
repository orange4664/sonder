#!/usr/bin/env python3
"""
learn.py — soft, context-scoped preference learning for hardworking-paper-writer.

This is a direct port of the "gets to know you" mechanism used by Metasequoia IME
(user_dictionary_journal.cpp) and corroborated by libpinyin (pinyin_remember_user_input
+ pinyin_train) and ZFVimIM (historical-frequency re-ranking).

The IME core, in plain terms:
  - Every time you pick a candidate, it RECORDS that choice in a user dictionary
    with a context key and a weight.
  - Nothing changes until the same choice in the same context is picked enough
    times (trigger_count).
  - When it does, the chosen candidate's weight is nudged upward, so it ranks a
    little higher NEXT time — but the other candidates stay available. A word is
    never banned.
  - The nudge is CONTEXT-LIMITED: "我吃" and "我是" learn independently, because
    their context keys differ.

Here the "candidate" is a rewrite option I propose for a sentence, the "context"
is the sentence's job in the argument, and the "pick" is the author's choice.
So "熟悉" means: gently shift which rewrite options I lead with in similar
sentences, without ever turning one "don't do that" into a global rule.

Run via CLI. Every command talks to the same SQLite database (preference.db).
Usage:
  python3 learn.py record <db> <section> <role> <opt> <weight>
  python3 learn.py signal <db> <section> <role> <opt> [weight]
  python3 learn.py bias   <db> <section> <role> [options...]
  python3 learn.py prune  <db>
  python3 learn.py reshow <db>
"""

import json
import os
import sqlite3
import sys

# --- Tunables (mirror the IME's constants) -------------------------------
TRIGGER_COUNT = 3          # picks before a preference starts to matter
BASE_GAP = 1000            # min spacing between ranked options (IME kRebalanceGap)
FLOOR = 1                  # lowest managed weight
CEILING = 100_000_000      # highest managed weight (IME kManagedWeightCeiling)

SCHEMA = """
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
"""


def _clamp(w):
    return max(FLOOR, min(CEILING, w))


def _open(db):
    os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    return conn


def _context(section, role):
    # section: abstract | intro | methods | results | discussion | conclusion
    # role   : topic | support | transition | conclusion | detail | hedge
    return f"{section}::{role}"


def _rows(conn, context):
    return conn.execute(
        "SELECT opt, weight, picks FROM prefs WHERE context=? AND opt!='__GLOBAL__'",
        (context,),
    ).fetchall()


def _weight_of(conn, context, opt):
    r = conn.execute(
        "SELECT weight FROM prefs WHERE context=? AND opt=?", (context, opt)
    ).fetchone()
    return r[0] if r else 0


def _bump(conn, context, opt, delta, picks_delta=1):
    # For the INSERT row: weight starts at _clamp(delta), picks starts at picks_delta.
    # For the ON CONFLICT update: weight += delta (floored at _clamp(delta)),
    # picks += picks_delta. Keep the two parameter groups separate and labelled.
    import sqlite3 as _s
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
    """The author declined/rejected `opt` here. Nudge it DOWN, never ban it.

    This is the difference between this mechanism and a kill-list: a rejected
    option is de-prioritized, not removed. It can come back if the author
    changes their mind, which is exactly what the IME words do.
    """
    conn = _open(db)
    w = _weight_of(conn, _context(section, role), opt)
    # Never let a single rejection drive a choice below the floor / into "banned".
    _bump(conn, _context(section, role), opt, max(int(weight) or -1000, FLOOR - w), 0)
    conn.commit()
    conn.close()


def global_pref(db, opt, weight=100_000):
    """The author explicitly said 'always/l never'. This is the ONLY global rule,
    and it must only be set when the author says so — like force_top in the IME.
    """
    conn = _open(db)
    _bump(conn, "__GLOBAL__", opt, int(weight) or 100_000, 0)
    conn.commit()
    conn.close()


def bias(db, section, role, options):
    """Return a soft ranking for `options` in this context: the weight each option
    currently has, plus its picks count. The caller uses this to ORDER its
    proposal (lead with the higher weights) WITHOUT dropping any option."""
    conn = _open(db)
    ctx = _context(section, role)
    out = {}
    for opt in options:
        w, p = _weight_of(conn, ctx, opt), 0
        r = conn.execute(
            "SELECT picks FROM prefs WHERE context=? AND opt=?", (ctx, opt)
        ).fetchone()
        if r:
            p = r[0]
        out[opt] = {"weight": w, "picks": p}
    # Global prefs (force-top) override only when the author made them global.
    g = conn.execute("SELECT opt, weight FROM prefs WHERE context='__GLOBAL__'").fetchall()
    for opt, w in g:
        if opt in out:
            out[opt]["weight"] = max(out[opt]["weight"], w)
    conn.close()
    return out


def prune(db, keep_recent=30):
    """Drop context/opt pairs that have not been exercised in a while, so stale
    preferences don't accumulate. Mirrors the IME's rebalance that keeps the
    managed weight range tight."""
    conn = _open(db)
    conn.execute(
        "DELETE FROM prefs WHERE context!='__GLOBAL__' AND "
        "(picks<? OR last_at<datetime('now','-30 days'))",
        (keep_recent,),
    )
    conn.commit()
    conn.close()


def reshow(db):
    conn = _open(db)
    rows = conn.execute(
        "SELECT context, opt, weight, picks FROM prefs "
        "WHERE context!='__GLOBAL__' AND picks>0 ORDER BY last_at DESC"
    ).fetchall()
    conn.close()
    print(json.dumps(
        [{"context": c, "opt": o, "weight": w, "picks": p} for c, o, w, p in rows],
        ensure_ascii=False,
        indent=2,
    ))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
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
    elif cmd == "prune":
        prune(sys.argv[2])
        print("ok")
    elif cmd == "reshow":
        reshow(sys.argv[2])
    else:
        print("unknown command; see --help", file=sys.stderr)


if __name__ == "__main__":
    main()
