-- Schema from library/learn.py at 494dba9 (before unique exemplar keys).
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
