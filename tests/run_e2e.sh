#!/usr/bin/env bash
# End-to-end test for hardworking-paper-writer's revision loop.
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

echo "[5] rewrite applied to working/ (filler + throat-clearing removed)"
grep -q "improves node-classification accuracy over existing approaches" tests/demo-paper-revision/working/demo-paper.md \
  && grep -q "Existing methods rely on a preconstructed graph" tests/demo-paper-revision/working/demo-paper.md \
  && echo "    OK working/ reflects the Medium rewrites"

echo "E2E PASS"
