#!/usr/bin/env bash
# Start, stop or connect to a private PostgreSQL 17 for the access-tag benchmarks.
#
# The server listens only on a Unix socket in $BENCH_DIR (no TCP), so it needs
# no container runtime and no root. PostgreSQL comes from conda-forge through
# `pixi exec`. nss_wrapper supplies a passwd entry for the current uid, which
# initdb needs and which sandboxes without a passwd entry lack.
#
# Usage: postgres.sh start|stop|restart|status|psql [psql args...]
#
# `restart` empties PostgreSQL's shared buffers (bench.py --cold uses it). The
# operating system's file cache is not emptied: that needs root.
set -euo pipefail

BENCH_DIR=${BENCH_DIR:-/tmp/opencode/access_tag_bench}
BENCH_PORT=${BENCH_PORT:-55432}
DATA="$BENCH_DIR/pgdata"
PIXI=(pixi exec -s postgresql=17 -s nss_wrapper --)

mkdir -p "$BENCH_DIR"

with_nss() {
    # Run a PostgreSQL binary with a passwd/group entry for the current user.
    local passwd="$BENCH_DIR/passwd" group="$BENCH_DIR/group"
    echo "bench:x:$(id -u):$(id -g):bench:$BENCH_DIR:/bin/sh" >"$passwd"
    echo "bench:x:$(id -g):" >"$group"
    "${PIXI[@]}" bash -c '
        export LD_PRELOAD="$CONDA_PREFIX/lib/libnss_wrapper.so"
        export NSS_WRAPPER_PASSWD="$1" NSS_WRAPPER_GROUP="$2"
        shift 2
        exec "$@"' with_nss "$passwd" "$group" "$@"
}

case "${1:-}" in
start)
    if [ ! -d "$DATA" ]; then
        with_nss initdb -D "$DATA" -U postgres --auth=trust >/dev/null
    fi
    with_nss pg_ctl -D "$DATA" -l "$BENCH_DIR/postgres.log" -w start -o \
        "-p $BENCH_PORT -k $BENCH_DIR -c listen_addresses='' \
         -c shared_buffers=512MB -c work_mem=32MB"
    ;;
stop)
    with_nss pg_ctl -D "$DATA" -m fast stop
    ;;
restart)
    "$0" stop >/dev/null
    "$0" start >/dev/null
    ;;
status)
    with_nss pg_ctl -D "$DATA" status
    ;;
psql)
    shift
    exec psql -h "$BENCH_DIR" -p "$BENCH_PORT" -U postgres "$@"
    ;;
*)
    echo "usage: $0 start|stop|restart|status|psql [args...]" >&2
    exit 2
    ;;
esac
