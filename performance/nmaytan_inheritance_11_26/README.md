# Access-tag inheritance benchmarks

This harness measures what access-tag inheritance costs in tiled's catalog on
PostgreSQL, for reads and writes. It compares a version with inheritance
against the version before it, or any two tiled checkouts.

With inheritance, a node that has no access tags of its own gets the tags of
its nearest tagged ancestor. The catalog root's tags are not inherited. The
catalog keeps a table of untagged nodes (`untagged_nodes`), maintained by
triggers, so that queries can find them through an index.

It measures:

- listing, counting, paging, searching and sorting the children of a
  container, with the access filter applied;
- deep search ("search everything") from the root and from containers;
- resolving a path one segment at a time, as every request does;
- building search responses, including the `effective_access_tags` field;
- creating, re-tagging and deleting nodes, which run the triggers.

It does not measure HTTP, serialization, the client, authentication, or the
access policy's own tag lookups: a stub policy grants the scenario's tags.

## Quick start

```sh
cd performance/nmaytan_inheritance_11_26
./postgres.sh start                       # private PostgreSQL 17, Unix socket only
./build.sh bench_new ../..                # this checkout's schema + all datasets
./build.sh bench_base /path/to/base       # the version before inheritance
./run.sh /path/to/base --db bench_base --label base --json results/base.json
./run.sh ../.. --db bench_new --label new --json results/new.json --plans plans/new
python3 compare.py results/base.json results/new.json
pixi exec -s matplotlib -s numpy -s graphviz python report.py \
    results/base.json results/new.json -o results/report.html
```

Each database must be built by the checkout that is measured against it,
because each version creates its own schema, indexes and triggers. A build
takes about 30 minutes, and a full run of the 500 scenarios a few hours per
checkout.

## How it measures

`bench.py` runs each scenario through the checkout's own request-path code:

- the access filter is applied by `tiled.server.utils.filter_for_access`;
- paths are resolved by `tiled.server.dependencies.get_entry`;
- response scenarios build the `/search` or `/search-deep` response with
  `tiled.server.core.construct_entries_response` and every field, the same
  default set the server uses.

A stub policy grants the scenario's tags (or everything, for admin
scenarios). Its `allowed_scopes` makes the same database call
`TagBasedAccessPolicy` makes: the node's effective tags where the checkout
has them, and its own tags otherwise.

For each scenario, `bench.py` records:

- the median and minimum time of `--runs` runs (default 7), after one warm-up
  run;
- the number of SQL statements in a run;
- the number of results (`-1` for not found);
- a fingerprint of the results, so that two versions can be checked to have
  found the same thing.

With `--plans DIR`, it also saves `EXPLAIN (ANALYZE, BUFFERS)` of every
`SELECT` from the last run, in `DIR/<scenario>.txt`.

Safeguards:

- **Timeout:** every statement has a timeout (`--timeout`, default 120 s); a
  scenario that hits it is recorded as `timeout`.
- **Slow plans:** a warm-up run slower than `--slow` (default 10 s) is
  recorded as the only run, so one bad plan cannot stall the whole benchmark.
- **Custom plans:** tiled sets `plan_cache_mode = force_custom_plan` on its
  PostgreSQL connections. Every statement is therefore planned for its actual
  parameter values, never with a generic plan.

**Cold cache:** `--cold` restarts PostgreSQL before each read scenario and
times a single run. This empties PostgreSQL's shared buffers, but not the
operating system's file cache, which needs root. Cold numbers therefore show
the cost of the shared buffers being empty, not of reading from disk.

## Datasets

All datasets hang directly under the catalog root and load into one database
(`datasets/*.sql`, loaded in this order by `build.sh`). The default user holds
`public`, `user:alice`, `tagA`, `bench_user` and `grp_0`. Wherever inheritance
changes which nodes are visible, a dataset also has a fully tagged twin. The
twin shows the same nodes to every version, so it is the fair comparison for
the version without inheritance.

| Dataset | Tree | Why it is there |
|---|---|---|
| `base` | `bl/` (bl_tag) holding `P/` and `Q/` (tagA), 1M children each. P: 50% tagA, 30% untagged, 20% tagB. Q: 98% tagC, 1% tagA, 1% untagged | Large listings where many (P) or few (Q) children are visible. The user does not hold `bl_tag`, so a deep search from `bl/` starts at a container whose own tags do not match |
| `untagged_top` | `U/`, untagged, under the root, with 500,000 untagged children | The root's tags are not inherited, so U is hidden. This is the largest input to deep search's untagged-children check, and a 404 walk target |
| `inheritance` | `inherit_true/`, `inherit_false/`, `half_untagged/`, `half_match/` (200,000 children each); `deep/`, `deep_tagged/` (4 levels × 20); `chain/c/c/c/c` | Listing shapes: the inherited tags match, do not match, or apply to half the children. Mixed trees for deep search. Ported from `../dylan_inheritance_11_26` |
| `unique_keys` | `keyed/`, 200,000 children keyed `k<md5(i)>`: a third untagged, the rest grp_* | Single-key lookups with realistic keys. The other datasets repeat keys `n0`, `n1`, … in every container, which skews PostgreSQL's estimates |
| `deep_chain` | `deepchain/`: 20 nested untagged containers with 10,000 children at the bottom, plus a tagged twin | The nearest tagged ancestor is 21 levels up: affects walks, listings and effective tags |
| `many_tags` | `many/`, `many_tagged/`: 200,000 children with 3 of 200 tags each; one in five untagged in `many/` | Many tags per user (the scenarios grant 55) and per node |
| `runs` | `tla/raw/` (bench_user) with 50,000 runs tagged grp_*, each with 2 streams of 4 arrays, untagged; `tla_tagged/raw/`, its tagged twin | A wide catalog where a user sees 1% of the runs, spread through the id order: deep search must reject the subtrees of the other 99% |
| `grid` | `grid/<shape>_<visible>_<descendants>/`: 25 catalogs of runs, about 110,000 nodes each (see below) | Every combination of the factors that change the access-control SQL, so that none goes unmeasured |
| `hidden_block` | `block/l2/l3/` holding 1,000 visible children, then `X` (tagged other) with 500,500 untagged descendants, then 1,000 untagged children; `block_tagged/`, its tagged twin | One large hidden subtree below the root, whose ids form one block that a deep-search page must skip |
| `writes` | `writes/` (empty), `writes_big/` with 100,000 untagged children | Targets for the write scenarios, which restore them after every run |

The children of P, Q and U are bare `array` rows with no data source, which
keeps these large datasets quick to build. The server cannot build a response
for them, so the response scenarios use the other datasets, whose children
are containers.

**The grid.** `runs` and `grid` are built by one generator,
`pg_temp.catalog` in `datasets/functions.sql`: a container of runs, each with
levels of descendants. Its settings are the factors that change the SQL:

- **shape:** wide (10,000 runs × 2 streams × 4 arrays) or deep (100 runs ×
  10 × 10 × 10);
- **visible:** how the runs are tagged: `1pct`, `50pct` or `100pct` of them
  with a tag the user holds, or `inherit` (untagged);
- **descendants:** how the nodes below the runs are tagged: `tagged` like
  their run, `mixed` (grp_0 on odd ones, the rest untagged, so some tagged
  nodes sit under hidden runs) or `inheriting` (untagged).

`grid.sql` builds every combination (untagged runs have no tags to copy, so
`inherit` has no `tagged` variant), plus three wide trees whose search root
is itself untagged (`_uroot`). The other datasets stay hand-written: they
test one extreme each (1M children, the catalog root, 21 levels, realistic
keys, 55 tags) or a detail the generator does not have.

Node ids in `base` and `untagged_top` are fixed, because some scenarios page
from a given id, so those two datasets must load first. The build ends with
`VACUUM ANALYZE` at a statistics target of 1000. This keeps plans stable
across rebuilds and between the databases being compared.

## Scenarios and coverage

`bench.py --list` prints every scenario and what it covers. Names are
`<target>.<operation>`.

**Operations on a container** (with the access filter applied):

| Operation | What it does |
|---|---|
| `count` | Exact count |
| `keys_page` | First page of 100 keys (cursor pagination, as the server uses) |
| `keys_page_mid` | A page from the middle |
| `items_page` | First page of 100 adapters |
| `response` | The full search response with all fields, including `effective_access_tags` |
| `metadata_search` | `Key("color") == "blue"` |
| `full_text` | `FullText("blue")` |
| `sort_metadata` | First page sorted by a metadata field (offset pagination) |
| `distinct` | Distinct metadata values, structure families and specs, with counts |
| `deep_*` | The same, as a deep search from the container |

**Coverage:**

| Targets | Operations | What it covers |
|---|---|---|
| P, Q | count, pages | The visible-children condition on 1M children |
| raw, raw_tagged | all operations; deep search | A beamline catalog where the user sees 1% of 50,000 runs, with inheriting or tagged descendants |
| grid.* | all deep operations on every tree; all flat operations on one tree per run tagging | Every combination of shape, visible fraction and descendant tagging (heat maps in the report) |
| block, block_tagged | all deep operations, the page just before X, listing l3 | One large hidden block below the root |
| root+10/999/1000_untagged | deep count and pages | Untagged children of the root, added around the 1,000-id limit of the hidden-children lookup |
| inherit_true / inherit_false / half_untagged / half_match | all flat operations | The visible-children condition when the inherited tags match, do not match, or apply to half the children; queries, sorting and distinct with the filter |
| many, many_tagged | all operations | Tag matching with 55 user tags and 3 tags per node |
| deepchain_bottom, deepchain_tagged_bottom | all flat operations | Nearest-tagged-ancestor lookup 21 levels up, and effective tags for 100 entries |
| deepchain, deepchain_tagged | all deep operations | Deep search through a long untagged chain |
| root (with `bl_tag`) | all deep operations, a page inside U's ids | Deep search from `/`, where every untagged child of the root is hidden |
| bl | all deep operations | Deep search from a container whose own tags do not match |
| bl+tag | deep count, page | The same, where they match (the untagged-children check is skipped) |
| deep, deep_tagged | all deep operations | Deep-search blockers in a mixed tree; queries and sorting in deep search |
| walk.* | path resolution | Per-segment cost on tagged, untagged and 21-level paths; single-key lookups with repeated and unique keys; not-found results (untagged child of the root, hidden child, hidden container) |
| admin.* | count, page, response, deep | No filter: the cost of the query shape itself |
| write.* | create 100 untagged, tagged or nested nodes; re-tag or untag a container with 100,000 untagged children; delete a 10,000-node subtree | Trigger cost of `untagged_nodes` and the closure table |

### Why this coverage is enough

Inheritance changes four things in the catalog, and the scenarios are chosen
around them:

1. **The visible-children condition** for listings:
   - the inherited tags matching and not matching, for half and for most
     children;
   - with metadata queries, full-text search, sorting, distinct and cursors
     in the middle;
   - at 200,000 and 1,000,000 children.
2. **The deep-search condition:** tagged nodes without a match on the path,
   and untagged children of the search root whose inherited tags do not
   match. Covered:
   - from the root, where those children always hide their subtrees, with
     the largest such subtree (U, 500,000 nodes) and a page inside it;
   - from containers whose own tags match and do not match;
   - over mixed trees and long untagged chains, with queries and sorting.
3. **The nearest-tagged-ancestor lookup:** walks, listings, effective tags
   and `allowed_scopes`, both near the tagged ancestor and 21 levels below it.
4. **The triggers:** inserting untagged, tagged and nested nodes, changing
   and removing a large container's tags, and deleting a large mixed subtree.

Each of these is measured:

- where it costs the most (the largest container, the deepest chain, the
  largest hidden subtree);
- at both ends of tags matching (matching, not matching);
- with many tags as well as few.

The single-dataset choices above left combinations unmeasured: the `runs`
deep count regression only appeared where most whole subtrees are hidden. The
grid therefore measures every combination of shape, visible fraction and
descendant tagging, and `descriptions.py` keeps a coverage table (factor,
value, trees) that the report prints with links.

Every scenario whose visible nodes depend on inheritance has a tagged twin.
The twins show what the version before inheritance pays for the same results.
The admin scenarios show the cost of each query shape without any filter.

This harness does not check that results are correct. Fingerprints only show
whether two versions agree. Correctness needs a test against a model of
inheritance.

**Not covered yet:**

- SQLite, to be added after PostgreSQL;
- production data and production statistics;
- a truly cold cache, which needs root on the host;
- concurrent requests;
- the graph API's access checks.

## Reading the results

`compare.py BASE.json OTHER.json` prints one row per scenario. Each row shows:

- the median time of each run;
- the ratio to the first file;
- the statement counts;
- the result counts.

A result count marked `*` means the fingerprints differ: the versions found
different results. This is expected in these cases:

- where inheritance makes more nodes visible: P, Q, half_untagged,
  deep, deepchain, many, bl, root, raw, block and the grid's `mixed`,
  `inheriting` and `inherit` trees;
- where tagged nodes sit under a hidden one, which inheritance hides: the
  grid's `mixed` trees and deep;
- in walks that find an untagged node, which the version before inheritance
  cannot see.

Compare those scenarios with their tagged twins:

- `half_untagged` with `half_match`;
- `deep` with `deep_tagged`;
- `deepchain` with `deepchain_tagged`;
- `many` with `many_tagged`;
- `raw` with `raw_tagged`;
- `block` with `block_tagged`;
- each `grid.*_inheriting` and `grid.*_mixed` tree with the `_tagged` tree of
  the same shape and visibility.

Anywhere else, a `*` is a bug. Two runs of the same version must always match.

**In the saved plans,** look at:

- `Execution Time`;
- rows read compared with rows returned;
- anti-join and semi-join types;
- `SubPlan` or `InitPlan` nodes with high loop counts.

**Noise:** run-to-run noise is a few percent for fast scenarios and more for
those that use parallel workers. Compare medians from runs on the same
machine, and use `--runs 9` or more when a ratio is close to 1.

## Usage reference

| Script | Purpose |
|---|---|
| `postgres.sh start\|stop\|restart\|status\|psql [args]` | Private PostgreSQL 17 from conda-forge (through `pixi exec`), on a Unix socket in `$BENCH_DIR`. Needs no container or root |
| `build.sh DBNAME TILED_SRC [DATASET...]` | Drop and recreate a database with `TILED_SRC`'s schema, then load the datasets |
| `check.sh DBNAME` | Check every heap page and B-tree index of a database with `amcheck`; `build.sh` runs it after loading and fails if anything is damaged. Run it again after a benchmark run, before trusting the results. Build one database at a time: parallel builds in the sandbox have produced damaged pages |
| `run.sh TILED_SRC [bench.py options]` | Run `bench.py` with `tiled` imported from `TILED_SRC` |
| `compare.py BASE.json OTHER.json...` | Comparison table |
| `report.py BASE.json NEW.json -o FILE [--notes FRAGMENT]` | Self-contained HTML report: summary, coverage, a diagram and description of every tree, the Tiled operation and purpose of every scenario, ratio charts and grid heat maps. Run with `pixi exec -s matplotlib -s numpy -s graphviz python report.py ...` |
| `descriptions.py` | The trees' shapes and descriptions, the operations' descriptions and the coverage table, for the report |

`bench.py` options:

| Option | Meaning |
|---|---|
| `--db`, `--label` | Database and a name for the run (required) |
| `--json FILE` | Write the results |
| `--plans DIR` | Save plans |
| `--runs N` | Timed runs per scenario (default 7) |
| `--groups read,write` | Groups to run (default: both; reads first, because writes change table statistics) |
| `--only a,b` | Run only scenarios whose names contain one of these |
| `--cold` | Restart PostgreSQL before each read scenario, and time one run |
| `--timeout S` | Per-statement timeout (default 120 s) |
| `--slow S` | Warm-up runs slower than this are the only run (default 10 s) |
| `--list` | List the scenarios |

**Environment** (`env.sh`):

| Variable | Meaning |
|---|---|
| `BENCH_DIR` | Data, socket and logs (default `/tmp/opencode/access_tag_bench`) |
| `BENCH_PORT` | Socket port (default 55432) |
| `BENCH_PYTHON` | A Python with tiled's server dependencies (default: the repository's `py313` pixi environment) |
| `BENCH_PYLIB` | Extra packages missing from that environment (greenlet), installed on first use |

**Any checkout,** including an unpacked tarball, can be measured. A tarball
has no `tiled/_version.py`; copy one in from a git checkout. Commands run
from `$BENCH_DIR` with `PYTHONPATH` set to the checkout, so the checkout's
`tiled` package is imported rather than an installed one.

**The tests** in `tests/` can use the same server:

```sh
PGHOST=$BENCH_DIR PGPORT=$BENCH_PORT TILED_TEST_POSTGRESQL_URI=postgresql://postgres@ pytest ...
```

A few tests that parse the URI fail with this host-less form, and they fail
the same way on every version.

## Design choices these benchmarks check

These describe the current implementation, and which scenarios would show a
regression if it changed.

- **Listings use `node.id IN (<tagged with a match> UNION <untagged, if the
  inherited tags match>)`,** which PostgreSQL plans as one semi-join over two
  index scans. `UNION ALL` makes PostgreSQL misestimate the row count and
  probe the children one at a time. Scenarios: `P.*`, `half_untagged.*`.
- **Children of the root are listed with an `EXISTS` on their own tags
  only,** because the root's tags are not inherited.
- **Deep search** hides:
  - nodes with a non-matching tagged node on their path below the search
    root, with one anti-join over the closure table;
  - nodes below an untagged child of the search root whose inherited tags do
    not match, with an uncorrelated `NOT IN`. PostgreSQL builds that set once
    per query, and skips it when the inherited tags match.

  The cost of the second part grows with the size of the hidden subtrees:
  `root.*` with U. Scenarios: `root.*`, `bl.*`, `deep.*`.
- **Whether the inherited tags match is decided in SQL** (a subquery
  evaluated once per query), not by the server ahead of time. Resolving a
  path therefore costs one statement per segment for the filter.
  Scenarios: `walk.*` (statement counts).
- **`effective_access_tags` in responses** is looked up for a whole page in
  one query, grouped by parent. Scenarios: `*.response`, `*_bottom.response`.
