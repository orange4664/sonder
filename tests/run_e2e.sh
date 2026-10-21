#!/usr/bin/env bash
# End-to-end test for sonder's revision loop.
# This reproduces the Phase 1→2→3 flow on a tiny demo paper WITHOUT needing a
# user at the prompt: it acts as the "author", recording a couple of choices,
# verifying the soft-store (bias), and the sentence-vocabulary (nearest).
#
# Run:  bash tests/run_e2e.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DB="tests/demo-paper-revision/preference.db"
LEARN="library/learn.py"   # path only, exactly as SKILL.md Phase 0 defines it
PAPER="tests/demo-paper.md"

rm -rf tests/demo-paper-revision
mkdir -p tests/demo-paper-revision/original tests/demo-paper-revision/working
cp "$PAPER" tests/demo-paper-revision/original/
cp "$PAPER" tests/demo-paper-revision/working/

# Phase 3, ¶1 — abstract
echo "[1] S001 author keeps original -> store + bias"
python3 -I "$LEARN" store "$DB" author "Graph neural networks have emerged as a powerful tool for node classification."
python3 -I "$LEARN" record "$DB" abstract topic "Keep original" 800
python3 -I "$LEARN" nearest "$DB" "Graph neural network models are now a key tool for classifying nodes." 3 | grep -q "\"kind\": \"author\"" \
  && echo "    OK nearest finds the author anchor"

echo "[2] S002 author picks Medium, verify bias leads Medium"
python3 -I "$LEARN" record "$DB" abstract detail Medium 1000
python3 -I "$LEARN" bias "$DB" abstract detail Light Medium Bold | grep -q '"Medium": {"weight": 999' \
  && echo "    OK Medium now leads in abstract::detail"

echo "[3] paragraph pause prune keeps fresh one-off preference"
python3 -I "$LEARN" prune "$DB"
python3 -I "$LEARN" bias "$DB" abstract detail Light Medium Bold | grep -q '"Medium": {"weight": 999' \
  && echo "    OK Medium survived paragraph pause"

echo "[4] Phase 3, ¶2 — intro, author picks Medium on S003, keeps S004"
python3 -I "$LEARN" record "$DB" intro topic Medium 1000
python3 -I "$LEARN" store "$DB" author "To address this, we propose a novel graph construction strategy based on semantic similarity."
python3 -I "$LEARN" prune "$DB"
python3 -I "$LEARN" nearest "$DB" "We propose a strategy built on semantic similarity to address this." 2 | grep -q '"id": 2' \
  && echo "    OK nearest retrieves the S004 author sentence"

echo "[6] regression: record works in the 4-arg form SKILL.md documents (no weight)"
python3 -I "$LEARN" record "$DB" methods detail Light
python3 -I "$LEARN" bias "$DB" methods detail Light Medium Bold | grep -q '"Light": {"weight": 999' \
  && echo "    OK 4-arg record stores weight 1000 by default"

echo "[7] regression: Chinese sentences participate in nearest (were stripped to empty)"
python3 -I "$LEARN" store "$DB" author "我们测量了三个站点的迁移率，没有观察到位错累积。"
python3 -I "$LEARN" store "$DB" author "本方法在未见场景下也能泛化。"
ZH_SIM=$(python3 -I "$LEARN" nearest "$DB" "我们测量了三个站点的迁移率" 1 | python3 -c 'import sys,json; print(json.load(sys.stdin)[0]["sim"])')
python3 -c "import sys; sys.exit(0 if float('$ZH_SIM') > 0.5 else 1)" \
  && echo "    OK Chinese nearest sim=$ZH_SIM (>0.5)"

echo "[8] regression: signal on a never-chosen option must not create a +1 entry"
python3 -I "$LEARN" signal "$DB" methods detail Bold
python3 -I "$LEARN" bias "$DB" methods detail Light Medium Bold | grep -q '"Bold": {"weight": 0, "picks": 0}' \
  && echo "    OK rejected-but-never-chosen option stays at 0"

echo "[9] regression: re-storing the same sentence reinforces (rewards 2), no duplicate row"
python3 -I "$LEARN" store "$DB" author "我们测量了三个站点的迁移率，没有观察到位错累积。"
python3 -I "$LEARN" list "$DB" author | python3 -c '
import sys, json
rows = json.load(sys.stdin)
zh = [r for r in rows if r["text"].startswith("我们测量")]
assert len(zh) == 1, f"duplicate rows: {len(zh)}"
assert zh[0]["rewards"] == 2, f"rewards={zh[0]["rewards"]}"
print("    OK single row, rewards=2")'

echo "[10] regression: prune forgets an OLD heavily-picked pref (was immortal when picks>2)"
P10="tests/demo-paper-revision/p10.db"
for _ in 1 2 3; do python3 -I "$LEARN" record "$P10" intro topic Medium >/dev/null; done
python3 - "$P10" <<'PY'
import sqlite3, sys, datetime
c = sqlite3.connect(sys.argv[1])
old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=400)).isoformat()
c.execute("update prefs set last_at=?", (old,)); c.commit()
PY
python3 -I "$LEARN" prune "$P10" >/dev/null
python3 - "$P10" <<'PY'
import sqlite3, sys
n = sqlite3.connect(sys.argv[1]).execute("select count(*) from prefs").fetchone()[0]
assert n == 0, f"old 3-pick pref survived prune: {n} rows"
print("    OK 400-day-old 3-pick pref was forgotten")
PY

echo "[11] regression: prune KEEPS a 40-day-old pref that is still heavy (decayed weight > floor)"
P11="tests/demo-paper-revision/p11.db"
python3 -I "$LEARN" record "$P11" intro topic Bold 100000 >/dev/null
python3 - "$P11" <<'PY'
import sqlite3, sys, datetime
c = sqlite3.connect(sys.argv[1])
old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=40)).isoformat()
c.execute("update prefs set last_at=?", (old,)); c.commit()
PY
python3 -I "$LEARN" prune "$P11" >/dev/null
python3 -I "$LEARN" bias "$P11" intro topic Bold | grep -q '"Bold": {"weight": 39685' \
  && echo "    OK heavy-but-aging pref kept (decayed to 39685)"

echo "[12] regression: signal with a POSITIVE weight still dips (was raising the rejected option)"
P12="tests/demo-paper-revision/p12.db"
python3 -I "$LEARN" record "$P12" intro topic Light 2000 >/dev/null
python3 -I "$LEARN" signal "$P12" intro topic Light 500 >/dev/null
python3 -I "$LEARN" bias "$P12" intro topic Light | grep -q '"Light": {"weight": 1499' \
  && echo "    OK signal +500 lowered 2000 -> 1499"

echo "[13] regression: store refuses empty / punctuation-only sentences"
P13="tests/demo-paper-revision/p13.db"
if python3 -I "$LEARN" store "$P13" author "" 2>/dev/null; then echo "    FAIL empty store accepted"; exit 1; fi
if python3 -I "$LEARN" store "$P13" author "!!! ..." 2>/dev/null; then echo "    FAIL punct-only store accepted"; exit 1; fi
echo "    OK empty and punctuation-only sentences refused"

echo "[14] regression: record weight 0 is an error, not a silent default"
if python3 -I "$LEARN" record "$P13" intro topic Bold 0 2>/dev/null; then echo "    FAIL weight 0 accepted"; exit 1; fi
echo "    OK weight 0 rejected"

echo "[15] docs-as-test: every \python3 -I "$LEARN" form documented in SKILL.md runs as written"
P15="tests/demo-paper-revision/p15.db"
python3 -I "$LEARN" backend "$P15" ngram >/dev/null
python3 -I "$LEARN" store   "$P15" author "A documented sentence." >/dev/null
python3 -I "$LEARN" record  "$P15" intro topic Medium >/dev/null
python3 -I "$LEARN" bias    "$P15" intro topic Light Medium Bold "Keep original" >/dev/null
python3 -I "$LEARN" nearest "$P15" "A documented sentence." 3 >/dev/null
python3 -I "$LEARN" prune   "$P15" >/dev/null
python3 -I "$LEARN" reshow  "$P15" >/dev/null
echo "    OK backend/store/record/bias/nearest/prune/reshow all accept the documented argument forms"

echo "[5] rewrite applied to working/ (filler + throat-clearing removed)"
grep -q "improves node-classification accuracy over existing approaches" tests/demo-paper-revision/working/demo-paper.md \
  && grep -q "Existing methods rely on a preconstructed graph" tests/demo-paper-revision/working/demo-paper.md \
  && echo "    OK working/ reflects the Medium rewrites"

echo "E2E PASS"
