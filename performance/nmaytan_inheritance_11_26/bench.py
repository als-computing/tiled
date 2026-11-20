"""Benchmark access-tag filtering in a tiled checkout, on PostgreSQL.

Run through run.sh, which takes the `tiled` package from the checkout being
measured. See README.md for the datasets, the scenarios and how to compare
two checkouts.

Every scenario goes through the checkout's own request-path code: the access
filter is applied by `tiled.server.utils.filter_for_access`, paths are
resolved by `tiled.server.dependencies.get_entry`, and response scenarios
build the response with `tiled.server.core.construct_entries_response`. A stub
access policy grants the scenario's tags, so no authentication or tag
definitions are needed. For each scenario this records the median and minimum
time over `--runs` runs (after one warm-up run), the number of SQL statements,
the number of results and a fingerprint of them.
"""

import argparse
import asyncio
import collections
import hashlib
import json
import os
import statistics
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import asyncpg
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

import tiled
from tiled.access_control.protocols import ApprovedAccessTags
from tiled.access_control.scopes import ALL_SCOPES
from tiled.catalog import from_uri
from tiled.queries import AccessTagsFilter, FullText, Key
from tiled.query_registration import default_query_registry
from tiled.server import schemas
from tiled.server.core import construct_entries_response
from tiled.server.dependencies import PaginationParams, get_entry
from tiled.server.utils import filter_for_access

HERE = Path(__file__).resolve().parent
PAGE = 100
# Tags held by the default benchmark user, across all datasets.
USER_TAGS = ["public", "user:alice", "tagA", "bench_user", "grp_0"]
MANY_TAGS = USER_TAGS + [f"mt_{i}" for i in range(50)]
# Writes per write scenario run, so that each run takes long enough to time.
WRITES = 100


# --- The access policy and request-path helpers --------------------------------


class StubPolicy:
    """Grants exactly `tags`, or everything for an admin.

    `allowed_scopes` makes the database call that TagBasedAccessPolicy makes
    for the node it checks: the node's effective tags where the checkout has
    them, otherwise its own tags.
    """

    def __init__(self, tags, admin=False):
        self.tags = list(tags)
        self.admin = admin

    async def filters(self, node, principal, authn_access_tags, authn_scopes, scopes):
        if self.admin:
            return []  # ALL_ACCESS
        return [AccessTagsFilter(list(self.tags))]

    async def allowed_scopes(self, node, principal, authn_access_tags, authn_scopes):
        if hasattr(node, "effective_access_tags"):
            await node.effective_access_tags()
        else:
            getattr(node, "access_tags", None)
        return set(ALL_SCOPES)


def new_metrics():
    "A fresh per-request metrics dict, shaped as the server makes it."
    return collections.defaultdict(lambda: collections.defaultdict(lambda: 0))


async def restrict(entry, policy):
    "Apply the access filter to `entry`, as the server does on every request."
    return await filter_for_access(
        entry, policy, None, None, set(), ["read:metadata"], new_metrics()
    )


async def walk(root, path, policy):
    "Resolve `path` as the server does. Returns None if it is not found (404)."
    try:
        return await get_entry(
            path,
            ["read:metadata"],
            None,
            None,
            set(),
            root,
            {},
            new_metrics(),
            None,
            policy,
        )
    except HTTPException as err:
        if err.status_code == 404:
            return None
        raise


def page(cursor=None, offset=None):
    return PaginationParams(offset=offset, cursor=cursor, limit=PAGE)


async def response(tree, path, deep=False, cursor=None):
    "The /search (or /search-deep) response for a page, with all fields."
    resp, _, _ = await construct_entries_response(
        default_query_registry,
        tree,
        "/search",
        path,
        page(cursor),
        list(schemas.EntryFields),
        None,
        False,
        False,
        {},
        None,
        "http://bench/api",
        "application/json",
        max_depth=None,
        exact_count_limit=100,
        deep=deep,
    )
    return [resource.id for resource in resp.data]


# --- Scenarios -------------------------------------------------------------------


@dataclass
class Scenario:
    """One benchmark.

    `run(bench)` is timed and returns the results: a list of keys, a count,
    or None for "not found". `setup(bench)` and `teardown(bench)` run, untimed,
    around every run.
    """

    name: str
    group: str  # "read" or "write"
    run: Callable[["Bench"], Awaitable]
    covers: str
    setup: Callable[["Bench"], Awaitable] | None = None
    teardown: Callable[["Bench"], Awaitable] | None = None
    tags: list = field(default_factory=lambda: list(USER_TAGS))
    admin: bool = False


def listing_ops(path, cursor_of=None):
    """(op name, coroutine function(bench, tree)) for a filtered container.

    `tree` is the container with the access filter applied, as the server has
    it after `get_entry`. `cursor_of(bench)` gives the cursor for the
    `keys_page_mid` op: the id of a child halfway through.
    """
    path_str = "/".join(path)

    async def count(b, t):
        return await t.exact_len()

    async def keys_page(b, t):
        return (await t.keys_page(None, PAGE))[0]

    async def keys_page_mid(b, t):
        return (await t.keys_page(await b.cursor(path, deep=False), PAGE))[0]

    async def items_page(b, t):
        return [key for key, _ in (await t.items_page(None, PAGE))[0]]

    async def response_(b, t):
        return await response(t, path_str)

    async def metadata_search(b, t):
        return (await t.search(Key("color") == "blue").keys_page(None, PAGE))[0]

    async def full_text(b, t):
        return (await t.search(FullText("blue")).keys_page(None, PAGE))[0]

    async def sort_metadata(b, t):
        return await t.sort([("i", -1)]).keys_range(0, PAGE)

    async def distinct(b, t):
        result = await t.get_distinct(["color"], True, True, True)
        return sorted(
            (item["value"], item.get("count")) for item in result["metadata"]["color"]
        )

    return {
        "count": count,
        "keys_page": keys_page,
        "keys_page_mid": keys_page_mid,
        "items_page": items_page,
        "response": response_,
        "metadata_search": metadata_search,
        "full_text": full_text,
        "sort_metadata": sort_metadata,
        "distinct": distinct,
    }


def deep_ops(path):
    "As listing_ops, for a deep search from the filtered container."
    path_str = "/".join(path)

    async def deep_count(b, t):
        return await t.search_deep().exact_len()

    async def deep_keys_page(b, t):
        return (await t.search_deep().keys_page(None, PAGE))[0]

    async def deep_keys_page_mid(b, t):
        cursor = await b.cursor(path, deep=True)
        return (await t.search_deep().keys_page(cursor, PAGE))[0]

    async def deep_response(b, t):
        return await response(t.search_deep(), path_str, deep=True)

    async def deep_metadata_search(b, t):
        tree = t.search_deep().search(Key("color") == "blue")
        return (await tree.keys_page(None, PAGE))[0]

    async def deep_full_text(b, t):
        tree = t.search_deep().search(FullText("blue"))
        return (await tree.keys_page(None, PAGE))[0]

    async def deep_sort_metadata(b, t):
        return await t.search_deep().sort([("i", -1)]).keys_range(0, PAGE)

    return {
        "deep_count": deep_count,
        "deep_keys_page": deep_keys_page,
        "deep_keys_page_mid": deep_keys_page_mid,
        "deep_response": deep_response,
        "deep_metadata_search": deep_metadata_search,
        "deep_full_text": deep_full_text,
        "deep_sort_metadata": deep_sort_metadata,
    }


def container_scenarios(
    label,
    path,
    ops,
    covers,
    tags=None,
    admin=False,
    deep=False,
    setup=None,
    teardown=None,
):
    """Scenarios named `<label>.<op>` for the container at `path`.

    The container is looked up without filtering and then filtered, as
    `get_entry` filters the last segment of a path: that way containers the
    user cannot reach (inherit_false/) can still be listed. Reaching a node is
    measured separately by the walk scenarios. `setup` and `teardown` are
    passed on to every scenario.
    """
    available = deep_ops(path) if deep else listing_ops(path)
    scenarios = []
    for op in ops:
        fn = available[op]

        async def run(b, fn=fn, policy=StubPolicy(tags or USER_TAGS, admin)):
            tree = await restrict(await b.root.lookup_adapter(path), policy)
            return await fn(b, tree)

        scenarios.append(
            Scenario(
                f"{label}.{op}",
                "read",
                run,
                covers,
                setup=setup,
                teardown=teardown,
                tags=tags or list(USER_TAGS),
                admin=admin,
            )
        )
    return scenarios


def walk_scenario(name, path, covers, tags=None):
    policy = StubPolicy(tags or USER_TAGS)

    async def run(b):
        entry = await walk(b.root, path, policy)
        return None if entry is None else [path]

    return Scenario(name, "read", run, covers)


def keyed(i):
    "The key of keyed/'s child i (see datasets/unique_keys.sql)."
    return "k" + hashlib.md5(str(i).encode()).hexdigest()


FLAT_OPS = [
    "count",
    "keys_page",
    "keys_page_mid",
    "items_page",
    "response",
    "metadata_search",
    "full_text",
    "sort_metadata",
    "distinct",
]
DEEP_OPS = [
    "deep_count",
    "deep_keys_page",
    "deep_keys_page_mid",
    "deep_response",
    "deep_metadata_search",
    "deep_full_text",
    "deep_sort_metadata",
]
CHAIN_BOTTOM = ["c"] * 20
GRID_VISIBLE = ["1pct", "50pct", "100pct", "inherit"]
GRID_DESCENDANTS = ["tagged", "mixed", "inheriting"]


async def _page_before_x(name, b):
    "The deep-search page from `name` that starts after n999, just before X."
    if ("before_x", name) not in b.cursors:
        b.cursors["before_x", name] = await b.conn.fetchval(
            "SELECT n.id FROM nodes t "
            "JOIN nodes l2 ON l2.parent = t.id AND l2.key = 'l2' "
            "JOIN nodes l3 ON l3.parent = l2.id AND l3.key = 'l3' "
            "JOIN nodes n ON n.parent = l3.id AND n.key = 'n999' "
            "WHERE t.parent = 0 AND t.key = $1",
            name,
        )
    tree = await restrict(await b.root.lookup_adapter([name]), StubPolicy(USER_TAGS))
    return (await tree.search_deep().keys_page(b.cursors["before_x", name], PAGE))[0]


async def _add_root_untagged(n, b):
    await b.conn.execute(
        "INSERT INTO nodes(parent, key, structure_family, metadata, specs) "
        "SELECT 0, 'ru' || g, 'container', '{}', '[]' FROM generate_series(1, $1) g",
        n,
    )


async def _remove_root_untagged(b):
    await b.conn.execute("DELETE FROM nodes WHERE parent = 0 AND key ~ '^ru[0-9]+$'")


def read_scenarios():
    s = []
    # Large listings (base.sql). P: most children visible, 30% by
    # inheritance. Q: few visible.
    for name in ["P", "Q"]:
        s += container_scenarios(
            name,
            ["bl", name],
            ["count", "keys_page", "keys_page_mid"],
            "visible-children condition on 1M children",
        )
    # Listing shapes (inheritance.sql): the inherited tags match, do not
    # match, apply to half the children, and the tagged twin of the latter.
    for name in ["inherit_true", "inherit_false", "half_untagged", "half_match"]:
        s += container_scenarios(
            name,
            [name],
            FLAT_OPS,
            "visible-children condition, inherited tags match or not",
        )
    # Many tags (many_tags.sql): the user holds 55 tags, nodes have 3 each.
    for name in ["many", "many_tagged"]:
        s += container_scenarios(
            name,
            [name],
            FLAT_OPS,
            "tag matching with many user tags and several tags per node",
            tags=MANY_TAGS,
        )
        s += container_scenarios(
            name,
            [name],
            DEEP_OPS,
            "deep-search blockers with many tags",
            tags=MANY_TAGS,
            deep=True,
        )
    # The bottom of a 21-level untagged chain (deep_chain.sql), and its tagged
    # twin.
    for name in ["deepchain", "deepchain_tagged"]:
        s += container_scenarios(
            f"{name}_bottom",
            [name] + CHAIN_BOTTOM,
            FLAT_OPS,
            "nearest-tagged-ancestor lookup 21 levels up",
        )
        s += container_scenarios(
            name,
            [name],
            DEEP_OPS,
            "deep search through a long untagged chain",
            deep=True,
        )
    # Deep search from the catalog root, which "search everything" does. The
    # user also holds bl_tag, so bl/P/Q are in scope. U (untagged_top.sql) is
    # hidden: its 500,000 nodes are the untagged-child check's worst case.
    s += container_scenarios(
        "root",
        [],
        DEEP_OPS,
        "deep search from /: untagged children of the root hidden",
        tags=USER_TAGS + ["bl_tag"],
        deep=True,
    )

    async def root_page_in_u(b):
        policy = StubPolicy(USER_TAGS + ["bl_tag"])
        tree = await restrict(b.root, policy)
        # Ids from 5,000,000 are U's children: the page must skip them all.
        return (await tree.search_deep().keys_page(4_999_000, PAGE))[0]

    s.append(
        Scenario(
            "root.deep_keys_page_in_U",
            "read",
            root_page_in_u,
            "paging through a hidden untagged subtree",
            tags=USER_TAGS + ["bl_tag"],
        )
    )
    # Deep search from bl, whose own tag the user does not hold (the
    # untagged-child check applies) and does hold ("bl+tag": it is skipped).
    s += container_scenarios(
        "bl",
        ["bl"],
        DEEP_OPS,
        "deep search from a container whose tags do not match",
        deep=True,
    )
    s += container_scenarios(
        "bl+tag",
        ["bl"],
        ["deep_count", "deep_keys_page"],
        "deep search from a container whose tags match",
        tags=USER_TAGS + ["bl_tag"],
        deep=True,
    )
    # Deep search over mixed tagged/untagged trees, and the tagged twin.
    for name in ["deep", "deep_tagged"]:
        s += container_scenarios(
            name,
            [name],
            DEEP_OPS,
            "deep-search blockers in a mixed tree",
            deep=True,
        )
    # A wide catalog of runs, 1% of them visible (runs.sql). Listing raw is
    # the same in both trees; raw_tagged is the fair comparison for deep
    # search and walks.
    s += container_scenarios(
        "raw",
        ["tla", "raw"],
        FLAT_OPS,
        "a wide container, few children visible",
    )
    for name, tla in [("raw", "tla"), ("raw_tagged", "tla_tagged")]:
        s += container_scenarios(
            name,
            [tla, "raw"],
            DEEP_OPS,
            "deep search, most subtrees hidden",
            deep=True,
        )
        s.append(
            walk_scenario(
                f"walk.{name}_array",
                f"{tla}/raw/n0/n0/n0",
                "an array in a run, two levels below the top",
            )
        )
    # One hidden subtree of 500,500 nodes three levels down, between visible
    # siblings (hidden_block.sql), and its tagged twin.
    for name in ["block", "block_tagged"]:
        s += container_scenarios(
            name,
            [name],
            DEEP_OPS,
            "deep search past one large hidden subtree",
            deep=True,
        )
        s.append(
            Scenario(
                f"{name}.deep_keys_page_before_X",
                "read",
                partial(_page_before_x, name),
                "a deep-search page that must skip the whole hidden subtree",
            )
        )
        s += container_scenarios(
            f"{name}_l3",
            [name, "l2", "l3"],
            ["count", "keys_page", "keys_page_mid"],
            "listing a container that holds a large hidden subtree",
        )
        s.append(
            walk_scenario(
                f"walk.{name}_w0",
                f"{name}/l2/l3/w0",
                "a node three levels down, beside a large hidden subtree",
            )
        )
    # Untagged children of the catalog root are hidden, and deep search from
    # the root looks up their ids (up to MAX_HIDDEN_UNTAGGED_CHILDREN, 1000)
    # before the search. U is one already; these add more around the limit.
    for n in [10, 999, 1000]:
        s += container_scenarios(
            f"root+{n}_untagged",
            [],
            ["deep_count", "deep_keys_page", "deep_keys_page_mid"],
            f"deep search from / with {n + 1} hidden untagged children of the root",
            tags=USER_TAGS + ["bl_tag"],
            deep=True,
            setup=partial(_add_root_untagged, n),
            teardown=_remove_root_untagged,
        )
    # Every combination of shape, visible fraction and descendant tagging
    # (grid.sql). Listing a tree depends only on how its runs are tagged, so
    # only one tree per run tagging is listed.
    cells = [
        (shape, visible, descendants, "")
        for shape in ["wide", "deep"]
        for visible in GRID_VISIBLE
        for descendants in GRID_DESCENDANTS
        if not (visible == "inherit" and descendants == "tagged")
    ]
    cells += [
        ("wide", "1pct", descendants, "_uroot") for descendants in GRID_DESCENDANTS
    ]
    for shape, visible, descendants, suffix in cells:
        cell = f"{shape}_{visible}_{descendants}{suffix}"
        covers = f"{shape} catalog, runs {visible}, descendants {descendants}"
        if suffix:
            covers += ", searched from an untagged container"
        if descendants == "tagged" or (
            visible == "inherit" and descendants == "inheriting"
        ):
            s += container_scenarios(f"grid.{cell}", ["grid", cell], FLAT_OPS, covers)
        s += container_scenarios(
            f"grid.{cell}", ["grid", cell], DEEP_OPS, covers, deep=True
        )
        below = "/n0" * (3 if shape == "wide" else 4)
        s.append(walk_scenario(f"walk.grid_{cell}", f"grid/{cell}{below}", covers))
    # Path walks: what every request pays to reach its node.
    s += [
        walk_scenario(
            "walk.tagged_depth2", "inherit_true/n0", "single-key lookup, n0.. keys"
        ),
        walk_scenario(
            "walk.tagged_depth5", "chain/c/c/c/c", "per-segment cost, tagged path"
        ),
        walk_scenario(
            "walk.untagged_depth5",
            "deep/n1/n2/n2/n2",
            "per-segment cost, untagged path",
        ),
        walk_scenario(
            "walk.keyed_tagged", f"keyed/{keyed(100100)}", "single-key lookup"
        ),
        walk_scenario(
            "walk.keyed_untagged",
            f"keyed/{keyed(99999)}",
            "single-key lookup, inherited",
        ),
        walk_scenario(
            "walk.keyed_hidden",
            f"keyed/{keyed(100001)}",
            "single-key lookup, not visible (404)",
        ),
        walk_scenario(
            "walk.P_tagged",
            "bl/P/p500000",
            "single-key lookup among 1M children",
            tags=USER_TAGS + ["bl_tag"],
        ),
        walk_scenario(
            "walk.P_untagged",
            "bl/P/p500005",
            "single-key lookup among 1M children, inherited",
            tags=USER_TAGS + ["bl_tag"],
        ),
        walk_scenario(
            "walk.P_hidden",
            "bl/P/p500008",
            "single-key lookup among 1M children, not visible (404)",
            tags=USER_TAGS + ["bl_tag"],
        ),
        walk_scenario(
            "walk.untagged_root_child",
            "U/u1",
            "untagged child of the root is not found (404)",
        ),
        walk_scenario(
            "walk.hidden_container",
            "inherit_false/n0",
            "not found at the first segment (404)",
        ),
        walk_scenario(
            "walk.chain_depth21",
            "/".join(["deepchain"] + CHAIN_BOTTOM),
            "21 untagged segments",
        ),
        walk_scenario(
            "walk.chain_tagged_depth21",
            "/".join(["deepchain_tagged"] + CHAIN_BOTTOM),
            "21 tagged segments",
        ),
    ]
    # Admins have no access filter: the baseline cost of each query shape.
    s += container_scenarios(
        "admin.P",
        ["bl", "P"],
        ["count", "keys_page"],
        "no filter (admin)",
        admin=True,
    )
    s += container_scenarios(
        "admin.half_untagged",
        ["half_untagged"],
        ["response"],
        "no filter (admin)",
        admin=True,
    )
    s += container_scenarios(
        "admin.root",
        [],
        ["deep_keys_page", "deep_count"],
        "no filter (admin)",
        admin=True,
        deep=True,
    )
    return s


async def _writes_id(b):
    return await b.conn.fetchval(
        "SELECT id FROM nodes WHERE parent = 0 AND key = 'writes'"
    )


async def _clear_writes(b):
    await b.conn.execute("DELETE FROM nodes WHERE parent = $1", await _writes_id(b))


async def _set_tags(b, key, names):
    node = await b.conn.fetchval(
        "SELECT id FROM nodes WHERE parent = 0 AND key = $1", key
    )
    await b.conn.execute(
        "DELETE FROM node_access_tags_association WHERE node_id = $1", node
    )
    await b.conn.execute(
        "INSERT INTO node_access_tags_association(node_id, tag_id) "
        "SELECT $1, id FROM access_tags WHERE name = ANY($2)",
        node,
        names,
    )


def write_scenarios():
    s = []

    def create(name, covers, tags, nested):
        async def run(b):
            parent = await b.root.lookup_adapter(["writes"])
            access_tags = ApprovedAccessTags(tags) if tags else None
            for i in range(WRITES):
                child = await parent.create_node(
                    "container", {}, key=f"w{i}", access_tags=access_tags
                )
                if nested:
                    parent = child
            return WRITES

        s.append(Scenario(name, "write", run, covers, teardown=_clear_writes))

    create(
        f"write.create_untagged_x{WRITES}",
        "untagged-node trigger on insert, as `tiled register` creates nodes",
        None,
        nested=False,
    )
    create(
        f"write.create_tagged_x{WRITES}", "insert with a tag", ["grp_0"], nested=False
    )
    create(
        f"write.create_untagged_nested_x{WRITES}",
        "untagged inserts, each below the last (closure grows)",
        None,
        nested=True,
    )

    async def retag(b):
        node = await b.root.lookup_adapter(["writes_big"])
        await node.replace_metadata(access_tags=ApprovedAccessTags({"grp_1"}))
        return 1

    async def untag(b):
        node = await b.root.lookup_adapter(["writes_big"])
        await node.replace_metadata(access_tags=ApprovedAccessTags())
        return 1

    async def restore(b):
        await _set_tags(b, "writes_big", ["bench_user"])

    s.append(
        Scenario(
            "write.retag_container_100k_untagged_children",
            "write",
            retag,
            "tag change on a container with many untagged children",
            teardown=restore,
        )
    )
    s.append(
        Scenario(
            "write.untag_container_100k_untagged_children",
            "write",
            untag,
            "removing a container's tags (it becomes untagged)",
            teardown=restore,
        )
    )

    async def make_subtree(b):
        # 100 containers with 100 children each under writes/del; half of
        # each level untagged.
        writes = await _writes_id(b)
        await b.conn.execute(
            """
            WITH d AS (
                INSERT INTO nodes(parent, key, structure_family, metadata, specs)
                VALUES ($1, 'del', 'container', '{}', '[]') RETURNING id
            ), mid AS (
                INSERT INTO nodes(parent, key, structure_family, metadata, specs)
                SELECT d.id, 'm' || g, 'container', jsonb_build_object('i', g), '[]'
                FROM d, generate_series(0, 99) g RETURNING id
            )
            INSERT INTO nodes(parent, key, structure_family, metadata, specs)
            SELECT mid.id, 'n' || g, 'container', jsonb_build_object('i', g), '[]'
            FROM mid, generate_series(0, 99) g
            """,
            writes,
        )
        await b.conn.execute(
            """
            INSERT INTO node_access_tags_association(node_id, tag_id)
            SELECT n.id, t.id
            FROM nodes_closure c JOIN nodes n ON n.id = c.descendant,
                 access_tags t
            WHERE c.ancestor = (SELECT id FROM nodes WHERE parent = $1 AND key = 'del')
              AND c.depth >= 1 AND ((n.metadata->>'i')::int) % 2 = 1
              AND t.name = 'grp_1'
            """,
            writes,
        )
        b.state["del"] = await b.root.lookup_adapter(["writes", "del"])

    async def delete(b):
        await b.state.pop("del").delete(recursive=True)
        return 1

    s.append(
        Scenario(
            "write.delete_subtree_10k",
            "write",
            delete,
            "deleting a subtree, half of it untagged",
            setup=make_subtree,
            teardown=_clear_writes,
        )
    )
    return s


SCENARIOS = read_scenarios() + write_scenarios()


# --- Running -------------------------------------------------------------------


class Recorder:
    "Capture the SQL statements executed while `active` is True."

    def __init__(self, engine):
        self.active = False
        self.statements = []
        event.listen(engine.sync_engine, "before_cursor_execute", self._capture)

    def _capture(self, conn, cursor, statement, parameters, context, executemany):
        if self.active:
            self.statements.append((statement, parameters))

    def start(self):
        self.statements = []
        self.active = True

    def stop(self):
        self.active = False
        return self.statements


class Bench:
    def __init__(self, args):
        self.args = args
        socket_dir = os.environ["BENCH_DIR"]
        port = os.environ["BENCH_PORT"]
        self.uri = (
            f"postgresql+asyncpg://postgres@/{args.db}?host={socket_dir}&port={port}"
        )
        self.dsn = f"postgresql://postgres@/{args.db}?host={socket_dir}&port={port}"
        self.cursors = {}
        self.state = {}

    async def connect(self):
        self.root = from_uri(self.uri)
        for task in self.root.startup_tasks:
            await task()
        self.engine = self.root.context.engine
        timeout_ms = str(self.args.timeout * 1000)

        def set_timeout(dialect, record, cargs, cparams):
            # A connection parameter rather than a SET, which a rollback of the
            # connection's first transaction would undo.
            cparams.setdefault("server_settings", {})["statement_timeout"] = timeout_ms

        event.listen(self.engine.sync_engine, "do_connect", set_timeout)
        # Connections made during startup lack the timeout.
        await self.engine.dispose()
        self.recorder = Recorder(self.engine)
        self.conn = await asyncpg.connect(
            self.dsn, server_settings={"statement_timeout": timeout_ms}
        )

    async def close(self):
        await self.conn.close()
        for task in self.root.shutdown_tasks:
            await task()

    async def cursor(self, path, deep):
        "The id of the node halfway through `path`'s children (or descendants)."
        if (tuple(path), deep) not in self.cursors:
            node = 0
            for key in path:
                node = await self.conn.fetchval(
                    "SELECT id FROM nodes WHERE parent = $1 AND key = $2", node, key
                )
            depth = "depth >= 1" if deep else "depth = 1"
            self.cursors[tuple(path), deep] = await self.conn.fetchval(
                "SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY descendant) "
                f"FROM nodes_closure WHERE ancestor = $1 AND {depth}",
                node,
            )
        return self.cursors[tuple(path), deep]

    async def restart_postgres(self):
        "Empty PostgreSQL's shared buffers (not the operating system's cache)."
        await self.conn.close()
        await self.engine.dispose()
        subprocess.run([str(HERE / "postgres.sh"), "restart"], check=True)
        self.conn = await asyncpg.connect(
            self.dsn,
            server_settings={"statement_timeout": str(self.args.timeout * 1000)},
        )

    async def explain(self, statement, parameters):
        async with self.engine.connect() as conn:
            raw = await conn.get_raw_connection()
            rows = await raw.driver_connection.fetch(
                "EXPLAIN (ANALYZE, BUFFERS) " + statement, *(parameters or ())
            )
            await conn.rollback()
        return "\n".join(r[0] for r in rows)

    async def run_once(self, scenario):
        if scenario.setup is not None:
            await scenario.setup(self)
        self.recorder.start()
        start = time.perf_counter()
        try:
            results = await scenario.run(self)
        finally:
            elapsed = time.perf_counter() - start
            statements = self.recorder.stop()
            if scenario.teardown is not None:
                await scenario.teardown(self)
        return elapsed, results, statements

    async def measure(self, scenario):
        runs = 1 if self.args.cold else self.args.runs
        times = []
        try:
            if self.args.cold:
                await self.restart_postgres()
            else:
                # Warm-up. A slow one (a bad plan) is the only run.
                elapsed, results, statements = await self.run_once(scenario)
                if elapsed > self.args.slow:
                    times, runs = [elapsed], 0
            for _ in range(runs):
                elapsed, results, statements = await self.run_once(scenario)
                times.append(elapsed)
        except DBAPIError as err:
            if "statement timeout" not in str(err):
                raise
            return {"timeout": True, "times_ms": [t * 1000 for t in times]}, []
        return {
            "median_ms": statistics.median(times) * 1000,
            "min_ms": min(times) * 1000,
            "runs": len(times),
            "statements": len(statements),
            "results": count_results(results),
            "fingerprint": fingerprint(results),
        }, statements

    async def save_plans(self, scenario, statements):
        directory = Path(self.args.plans)
        directory.mkdir(parents=True, exist_ok=True)
        plans = []
        for i, (sql, params) in enumerate(statements):
            if not sql.lstrip().upper().startswith(("SELECT", "WITH")):
                continue
            try:
                plan = await self.explain(sql, params)
            except DBAPIError as err:
                plan = f"(EXPLAIN failed: {str(err).splitlines()[0]})"
            plans.append(
                f"-- statement {i}\n{sql}\n-- parameters: {params!r}\n\n{plan}\n"
            )
        (directory / f"{scenario.name}.txt").write_text("\n\n".join(plans))


def count_results(results):
    if results is None:
        return -1
    if isinstance(results, int):
        return results
    return len(results)


def fingerprint(results):
    "A short hash of the results, to check that two versions found the same."
    return hashlib.sha1(json.dumps(results, default=str).encode()).hexdigest()[:12]


def git_describe():
    here = Path(tiled.__file__).resolve().parent
    try:
        return subprocess.check_output(
            ["git", "-C", str(here), "log", "-1", "--format=%h %s"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def selected(args):
    result = []
    for scenario in SCENARIOS:
        if scenario.group not in args.groups:
            continue
        if args.cold and scenario.group == "write":
            continue
        if args.only and not any(part in scenario.name for part in args.only):
            continue
        result.append(scenario)
    return result


async def main(args):
    bench = Bench(args)
    await bench.connect()
    summary = {
        "label": args.label,
        "db": args.db,
        "tiled": str(Path(tiled.__file__).resolve().parent),
        "commit": git_describe(),
        "runs": args.runs,
        "cold": args.cold,
        "scenarios": {},
    }
    print(f"[{args.label}] tiled {summary['tiled']} ({summary['commit']})")
    try:
        for scenario in selected(args):
            record, statements = await bench.measure(scenario)
            record["group"] = scenario.group
            record["covers"] = scenario.covers
            summary["scenarios"][scenario.name] = record
            if record.get("timeout"):
                shown = f"{'timeout':>10}"
            else:
                found = (
                    "not found"
                    if record["results"] == -1
                    else f"{record['results']} results"
                )
                shown = (
                    f"{format_ms(record['median_ms']):>10}  "
                    f"{record['statements']:3} stmts  {found}"
                )
            print(f"  {scenario.name:52} {shown}", flush=True)
            if args.plans and statements and scenario.group == "read":
                await bench.save_plans(scenario, statements)
    finally:
        await bench.close()
        if args.json:
            Path(args.json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json).write_text(json.dumps(summary, indent=1))


def format_ms(ms):
    if ms >= 1000:
        return f"{ms / 1000:.2f} s"
    if ms >= 10:
        return f"{ms:.0f} ms"
    return f"{ms:.1f} ms"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", help="database built by build.sh (required)")
    parser.add_argument("--label", help="name for this run (required)")
    parser.add_argument("--json", help="write the results to this file")
    parser.add_argument("--plans", help="directory for EXPLAIN ANALYZE output")
    parser.add_argument("--runs", type=int, default=7, help="timed runs (median)")
    parser.add_argument(
        "--groups",
        type=lambda s: s.split(","),
        default=["read", "write"],
        help="comma-separated: read,write (default: both, reads first)",
    )
    parser.add_argument(
        "--only",
        type=lambda s: s.split(","),
        help="comma-separated parts of scenario names to run (default: all)",
    )
    parser.add_argument(
        "--cold",
        action="store_true",
        help="restart PostgreSQL before each scenario and time one run "
        "(read scenarios only)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="per-statement timeout in seconds (recorded as a timeout)",
    )
    parser.add_argument(
        "--slow",
        type=float,
        default=10,
        help="seconds: a warm-up run slower than this is the only run",
    )
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    args = parser.parse_args()
    if args.list:
        for scenario in SCENARIOS:
            print(f"{scenario.group:6} {scenario.name:52} {scenario.covers}")
    elif not (args.db and args.label):
        parser.error("--db and --label are required")
    else:
        asyncio.run(main(args))
