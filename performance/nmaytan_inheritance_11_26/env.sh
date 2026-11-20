# Shared settings for the access-tag benchmark scripts. Source, do not run.
#
# BENCH_PYTHON is a Python with tiled's server dependencies; the repository's
# pixi environment is used by default. The tiled package itself is taken from
# PYTHONPATH (the checkout being measured), so commands run from $BENCH_DIR:
# `python -c`/`-m` put the current directory first on sys.path.
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
export BENCH_DIR=${BENCH_DIR:-/tmp/opencode/access_tag_bench}
export BENCH_PORT=${BENCH_PORT:-55432}
export BENCH_PYTHON=${BENCH_PYTHON:-$REPO/.pixi/envs/py313/bin/python}
# Extra packages missing from the pixi environment (greenlet, for SQLAlchemy's
# asyncio support), installed here on first use.
export BENCH_PYLIB=${BENCH_PYLIB:-$BENCH_DIR/pylib}

mkdir -p "$BENCH_DIR"
if ! PYTHONPATH="$BENCH_PYLIB" "$BENCH_PYTHON" -c "import greenlet" 2>/dev/null; then
    pyver=$("$BENCH_PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    pixi exec -s "python=$pyver" -s pip -- \
        pip install --quiet --target "$BENCH_PYLIB" greenlet
fi
