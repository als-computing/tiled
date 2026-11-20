#!/usr/bin/env bash
# Check a benchmark database for damaged pages with PostgreSQL's amcheck:
# every heap page of the catalog's tables and every B-tree index on them.
# Exits non-zero if anything is wrong. build.sh runs it after loading.
#
# Usage: check.sh DBNAME
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
DBNAME=${1:?usage: check.sh DBNAME}

problems=$("$HERE/postgres.sh" psql -q "$DBNAME" -At -v ON_ERROR_STOP=1 <<'EOF'
CREATE EXTENSION IF NOT EXISTS amcheck;
SELECT c.relname || ' block ' || v.blkno || ': ' || v.msg
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL verify_heapam(c.oid) v
WHERE n.nspname = 'public' AND c.relkind = 'r';
SELECT count(bt_index_check(c.oid, true))
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_am am ON am.oid = c.relam
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE am.amname = 'btree' AND n.nspname = 'public';
EOF
) || {
    echo "check.sh: $DBNAME is damaged (amcheck failed)" >&2
    exit 1
}
# The last line is the number of indexes checked; anything before it is a
# damaged heap page.
if [ "$(printf '%s\n' "$problems" | wc -l)" -gt 1 ]; then
    printf '%s\n' "$problems" | head -20 >&2
    echo "check.sh: $DBNAME is damaged" >&2
    exit 1
fi
echo "check.sh: $DBNAME has no damaged pages ($problems B-tree indexes checked)"
