#!/usr/bin/env bash
# Run bench.py with the tiled package taken from a given checkout.
#
# Usage: run.sh TILED_SRC [bench.py args...]
#   e.g. run.sh ../.. --db bench_new --label new --json results/new.json
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/env.sh"

SRC=$(cd "${1:?usage: run.sh TILED_SRC [bench.py args...]}" && pwd)
shift
# bench.py runs from $BENCH_DIR (see env.sh), so make relative --json and
# --plans paths relative to the caller's directory.
CALLER=$(pwd)
cd "$BENCH_DIR"
args=()
prev=""
for arg in "$@"; do
    if [[ $prev == --json || $prev == --plans ]] && [[ $arg != /* ]]; then
        arg="$CALLER/$arg"
    fi
    args+=("$arg")
    prev=$arg
done
PYTHONPATH="$BENCH_PYLIB:$SRC" exec "$BENCH_PYTHON" "$HERE/bench.py" "${args[@]}"
