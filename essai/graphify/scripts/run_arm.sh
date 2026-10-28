#!/usr/bin/env bash
# usage: run_arm.sh <A|B> <qid> <question>
set -u
S=${EVAL_DIR:?set EVAL_DIR to the evaluation directory}
arm=$1; qid=$2; q=$3
suffix="Answer about this repository. End your answer with a line 'FILES:' listing the repo-relative paths of the source files that answer the question, and a line 'ADR:' listing decision record numbers (4 digits) if relevant. Do not modify anything."
if [ "$arm" = A ]; then
  dir=$S/corpusA; mcp=$S/mcp-none.json; tools="Read,Grep,Glob"
  prompt="$q

$suffix"
else
  if [ "$arm" = B2 ]; then dir=$S/corpus; mcp=$S/mcp-graphify-b2.json; else dir=$S/corpus; mcp=$S/mcp-graphify.json; fi; tools="Read,Grep,Glob,mcp__graphify"
  prompt="$q

A knowledge graph of this repository is available through the graphify MCP tools (query_graph, get_node, get_neighbors, shortest_path, god_nodes...). Use it to orient yourself before reading files.
$suffix"
fi
mkdir -p $S/runs
cd "$dir" && claude -p < /dev/null "$prompt" --model haiku --output-format json --strict-mcp-config --mcp-config "$mcp" \
  --allowedTools "$tools" --disallowedTools "Bash,Edit,Write,WebFetch,WebSearch,Task,Agent" \
  --max-turns 30 --max-budget-usd 0.30 > $S/runs/$arm-$qid.json 2> $S/runs/$arm-$qid.err
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(sys.argv[2], d.get('total_cost_usd'), d.get('num_turns'), d.get('duration_ms'), d.get('subtype'))" $S/runs/$arm-$qid.json $arm-$qid
