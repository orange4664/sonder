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
LEARN="python3 library/learn.py"
PAPER="tests/demo-paper.md"

rm -rf tests/demo-paper-revision
mkdir -p tests/demo-paper-revision/original tests/demo-paper-revision/working
cp "$PAPER" tests/demo-paper-revision/original/
cp "$PAPER" tests/demo-paper-revision/working/

# Phase 3, ¶1 — abstract
echo "[1] S001 author keeps original -> store + bias"
$LEARN store "$DB" author "Graph neural networks have emerged as a powerful tool for node classification."
$LEARN record "$DB" abstract topic "Keep original" 800
$LEARN nearest "$DB" "Graph neural network models are now a key tool for classifying nodes." 3 | grep -q "\"kind\": \"author\"" \
  && echo "    OK nearest finds the author anchor"

echo "[2] S002 author picks Medium, verify bias leads Medium"
$LEARN record "$DB" abstract detail Medium 1000
$LEARN bias "$DB" abstract detail Light Medium Bold | grep -q '"Medium": {"weight": 999' \
  && echo "    OK Medium now leads in abstract::detail"

echo "[3] paragraph pause prune keeps fresh one-off preference"
$LEARN prune "$DB"
$LEARN bias "$DB" abstract detail Light Medium Bold | grep -q '"Medium": {"weight": 999' \
  && echo "    OK Medium survived paragraph pause"

echo "[4] Phase 3, ¶2 — intro, author picks Medium on S003, keeps S004"
$LEARN record "$DB" intro topic Medium 1000
$LEARN store "$DB" author "To address this, we propose a novel graph construction strategy based on semantic similarity."
$LEARN prune "$DB"
$LEARN nearest "$DB" "We propose a strategy built on semantic similarity to address this." 2 | grep -q '"id": 2' \
  && echo "    OK nearest retrieves the S004 author sentence"

echo "[6] regression: record works in the 4-arg form SKILL.md documents (no weight)"
$LEARN record "$DB" methods detail Light
$LEARN bias "$DB" methods detail Light Medium Bold | grep -q '"Light": {"weight": 999' \
  && echo "    OK 4-arg record stores weight 1000 by default"

echo "[7] regression: Chinese sentences participate in nearest (were stripped to empty)"
$LEARN store "$DB" author "我们测量了三个站点的迁移率，没有观察到位错累积。"
$LEARN store "$DB" author "本方法在未见场景下也能泛化。"
ZH_SIM=$($LEARN nearest "$DB" "我们测量了三个站点的迁移率" 1 | python3 -c 'import sys,json; print(json.load(sys.stdin)[0]["sim"])')
python3 -c "import sys; sys.exit(0 if float('$ZH_SIM') > 0.5 else 1)" \
  && echo "    OK Chinese nearest sim=$ZH_SIM (>0.5)"

echo "[8] regression: signal on a never-chosen option must not create a +1 entry"
$LEARN signal "$DB" methods detail Bold
$LEARN bias "$DB" methods detail Light Medium Bold | grep -q '"Bold": {"weight": 0, "picks": 0}' \
  && echo "    OK rejected-but-never-chosen option stays at 0"

echo "[9] regression: re-storing the same sentence reinforces (rewards 2), no duplicate row"
$LEARN store "$DB" author "我们测量了三个站点的迁移率，没有观察到位错累积。"
$LEARN list "$DB" author | python3 -c '
import sys, json
rows = json.load(sys.stdin)
zh = [r for r in rows if r["text"].startswith("我们测量")]
assert len(zh) == 1, f"duplicate rows: {len(zh)}"
assert zh[0]["rewards"] == 2, f"rewards={zh[0]["rewards"]}"
print("    OK single row, rewards=2")'

echo "[5] rewrite applied to working/ (filler + throat-clearing removed)"
grep -q "improves node-classification accuracy over existing approaches" tests/demo-paper-revision/working/demo-paper.md \
  && grep -q "Existing methods rely on a preconstructed graph" tests/demo-paper-revision/working/demo-paper.md \
  && echo "    OK working/ reflects the Medium rewrites"

echo "E2E PASS"
