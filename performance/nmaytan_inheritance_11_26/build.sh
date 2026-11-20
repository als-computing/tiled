#!/usr/bin/env bash
# (Re)create a benchmark database with the schema of a given tiled checkout,
# then load the datasets into it.
#
# Usage: build.sh DBNAME TILED_SRC [DATASET...]
#   DBNAME     database to (re)create, e.g. bench_new
#   TILED_SRC  root of the tiled checkout whose `tiled catalog init` makes the
#              schema, indexes and triggers (the directory that contains the
#              `tiled` package)
#   DATASET    names from datasets/ (without .sql); default: all, in the order
#              below. base and untagged_top use fixed node ids, so they must
#              come first.
#
# Takes about 13 minutes for all datasets (about 5.5M nodes).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/env.sh"

DBNAME=${1:?usage: build.sh DBNAME TILED_SRC [DATASET...]}
SRC=$(cd "${2:?usage: build.sh DBNAME TILED_SRC [DATASET...]}" && pwd)
shift 2
DATASETS=("$@")
if [ ${#DATASETS[@]} -eq 0 ]; then
    DATASETS=(base untagged_top inheritance unique_keys deep_chain many_tags runs grid hidden_block writes)
fi

"$HERE/postgres.sh" psql -q -c "DROP DATABASE IF EXISTS $DBNAME" \
    -c "CREATE DATABASE $DBNAME"
(cd "$BENCH_DIR" && PYTHONPATH="$BENCH_PYLIB:$SRC" "$BENCH_PYTHON" -m tiled \
    catalog init "postgresql+asyncpg://postgres@/$DBNAME?host=$BENCH_DIR&port=$BENCH_PORT")
for dataset in "${DATASETS[@]}"; do
    echo "Loading $dataset"
    start=$SECONDS
    "$HERE/postgres.sh" psql -q "$DBNAME" \
        -f "$HERE/datasets/functions.sql" -f "$HERE/datasets/$dataset.sql" >/dev/null
    echo "  $((SECONDS - start)) s"
done
# A larger statistics sample than the default (100) keeps the planner's
# estimates, and so its plans, the same across rebuilds and between the
# databases of the versions being compared.
"$HERE/postgres.sh" psql -q "$DBNAME" \
    -c "SET default_statistics_target = 1000" -c "VACUUM ANALYZE"
"$HERE/check.sh" "$DBNAME"
"$HERE/postgres.sh" psql -q "$DBNAME" -At -F ' ' -c "
    SELECT 'nodes', count(*) FROM nodes
    UNION ALL SELECT 'closure rows', count(*) FROM nodes_closure
    UNION ALL SELECT 'tag rows', count(*) FROM node_access_tags_association"
