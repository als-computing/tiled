"""Plain-language descriptions of the benchmark trees and operations.

report.py uses these to explain each scenario: the shape of the tree it runs
on (drawn as a diagram), the Tiled operation and why it matters. Scenario
names are `<tree>.<operation>` (see bench.py); walks and writes are
`walk.<target>` and `write.<operation>`.

A tree's `same_results` says whether base (without inheritance) and the
branch should find the same nodes: True for the tagged twins, where every
node carries its own tags; False where untagged nodes become visible by
inheritance, or tagged nodes under a hidden one become hidden; None where it
depends on the operation.
"""

# Node kinds in the shape diagrams.
VISIBLE = "visible"  # tagged with a tag the user holds
HIDDEN = "hidden"  # tagged, none of them held by the user
UNTAGGED = "untagged"  # no tags of its own: inherits
PARTIAL = "partial"  # siblings with different tags, some visible
CATALOG_ROOT = "root"

KIND_LEGEND = {
    VISIBLE: "tagged, the user holds the tag",
    HIDDEN: "tagged, the user holds none of the tags",
    UNTAGGED: "no tags of its own (inherits)",
    PARTIAL: "siblings tagged differently: some visible, some hidden",
    CATALOG_ROOT: "the catalog root",
}

USER = (
    "The benchmark user holds `public`, `user:alice`, `tagA`, `bench_user` and `grp_0`."
)

# (Tiled operation, why it is measured)
LISTING_OPS = {
    "count": (
        "Count a container's children (`len(container)`, the `count` of a listing)",
        "The access condition evaluated over every child: the worst case for a "
        "condition that is slow per row.",
    ),
    "keys_page": (
        "First page of a listing (`/search/{path}`, 100 keys)",
        "The most common request. It must stop after 100 visible children "
        "instead of checking them all.",
    ),
    "keys_page_mid": (
        "A page from the middle of a listing (cursor pagination)",
        "Paging deeper into a large container: the database must start at the "
        "cursor, not rescan the earlier children.",
    ),
    "items_page": (
        "First page of entries (`container.items()`)",
        "Loading 100 child adapters, as the Python client does when iterating.",
    ),
    "response": (
        "The full `/search/{path}` response for a page, all fields",
        "What a request really costs end to end: keys, metadata, structure, "
        "effective access tags and the count.",
    ),
    "metadata_search": (
        "Metadata search (`Key('color') == 'blue'`), first page",
        "A metadata query combined with the access condition; PostgreSQL must "
        "use both without scanning everything.",
    ),
    "full_text": (
        "Full-text search (`FullText('blue')`), first page",
        "The full-text index combined with the access condition.",
    ),
    "sort_metadata": (
        "Listing sorted by a metadata field, first page (offset pagination)",
        "Sorting means every visible child is considered before the first "
        "page: it cannot stop early in id order.",
    ),
    "distinct": (
        "Distinct metadata values, structure families and specs, with counts "
        "(`/distinct`)",
        "Facet counts aggregate over every visible child.",
    ),
}
DEEP = (
    "Deep search visits every descendant, not only children, and each "
    "candidate's whole path below the search root must pass. "
)
DEEP_OPS = {
    "deep_count": (
        "Deep search: count every visible descendant",
        DEEP + "Every candidate is checked: the worst case for the path check.",
    ),
    "deep_keys_page": (
        "Deep search (`/search/{path}` with deep search), first page of 100 keys",
        DEEP + "It must stop after 100 visible descendants.",
    ),
    "deep_keys_page_mid": (
        "Deep search, a page from the middle (cursor pagination)",
        DEEP + "It must start at the cursor, not rescan the earlier descendants.",
    ),
    "deep_response": (
        "Deep search: the full response for the first page, all fields",
        DEEP + "The end-to-end cost, including the count.",
    ),
    "deep_metadata_search": (
        "Deep search with a metadata query (`Key('color') == 'blue'`), first page",
        DEEP + "The metadata condition and the path check together.",
    ),
    "deep_full_text": (
        "Deep search with a full-text query (`FullText('blue')`), first page",
        DEEP + "The full-text index and the path check together.",
    ),
    "deep_sort_metadata": (
        "Deep search sorted by a metadata field, first page",
        DEEP + "Every visible descendant is considered before the first page.",
    ),
}
SPECIAL_OPS = {
    "deep_keys_page_in_U": (
        "Deep search from `/`, a page starting inside `U`",
        "The page must skip U's 500,000 hidden nodes, which are hidden because "
        "U is an untagged child of the catalog root.",
    ),
    "deep_keys_page_before_X": (
        "Deep search, the page that starts just before `X`",
        "The page must skip X's 500,500 hidden descendants before it finds the "
        "next visible node: one large hidden block below the root.",
    ),
}
WALK_OP = (
    "Resolve a path, as every request to `/metadata/{path}` (or any other "
    "route) does first",
    "The server looks up each segment and checks access at every level, so "
    "this cost is paid by every request, per level.",
)
OPS = {**LISTING_OPS, **DEEP_OPS, **SPECIAL_OPS}


def node(label, note, kind, *children):
    return (label, note, kind, list(children))


def under_root(*children):
    return node("/", "", CATALOG_ROOT, *children)


TREES = {}


def tree(label, title, description, shape, same_results, dataset, factors=()):
    TREES[label] = dict(
        title=title,
        description=description,
        shape=shape,
        same_results=same_results,
        dataset=dataset,
        factors=list(factors),
    )


# --- base.sql ------------------------------------------------------------------

BL = under_root(
    node(
        "bl",
        "bl_tag",
        HIDDEN,
        node(
            "P",
            "tagA",
            VISIBLE,
            node("p0..p999999", "1M: 50% tagA, 30% untagged, 20% tagB", PARTIAL),
        ),
        node(
            "Q",
            "tagA",
            VISIBLE,
            node("q0..q999999", "1M: 1% tagA, 1% untagged, 98% tagC", PARTIAL),
        ),
    )
)
tree(
    "P",
    "P: 1M children, most visible",
    "A very large container where half the children carry a tag the user "
    "holds, 30% are untagged (visible by inheritance) and 20% are hidden.",
    BL,
    False,
    "base",
    ["listing", "1M children", "50% visible"],
)
tree(
    "Q",
    "Q: 1M children, few visible",
    "A very large container where only 2% of the children are visible "
    "(1% tagged, 1% by inheritance), spread evenly: a page must reject many "
    "rows.",
    BL,
    False,
    "base",
    ["listing", "1M children", "1% visible"],
)
tree(
    "bl",
    "Deep search from bl, whose own tag the user lacks",
    "bl carries `bl_tag`, which the user does not hold; the scenario applies "
    "the filter to bl directly (as a Python user of the adapter could) and "
    "searches P's and Q's 2M nodes.",
    BL,
    False,
    "base",
    ["deep search", "search root's tags do not match", "2M nodes"],
)
tree(
    "bl+tag",
    "Deep search from bl, whose tag the user holds",
    "The same tree, for a user who also holds `bl_tag`.",
    BL,
    False,
    "base",
    ["deep search", "search root's tags match", "2M nodes"],
)
tree(
    "admin.P",
    "Admin listing of P",
    "No access filter at all: the cost of the query shape itself, for reference.",
    BL,
    True,
    "base",
    ["admin baseline"],
)

# --- untagged_top.sql and the catalog root ------------------------------------------

ROOT = under_root(
    node("bl, P, Q", "base.sql, 2M nodes", PARTIAL),
    node("U", "untagged child of /", UNTAGGED, node("u0..u499999", "", UNTAGGED)),
    node("every other tree", "about 6.7M nodes", PARTIAL),
)
tree(
    "root",
    "Deep search from the catalog root",
    '"Search everything": a deep search from `/` over every dataset (about '
    "9.2M nodes). The catalog root's tags are not inherited, so its untagged "
    "child U and U's 500,000 children are hidden. The user also holds "
    "`bl_tag` here.",
    ROOT,
    False,
    "untagged_top",
    ["deep search", "search root is /", "1 hidden untagged child of /"],
)
for n in [10, 999, 1000]:
    tree(
        f"root+{n}_untagged",
        f"Deep search from / with {n + 1} untagged children of the root",
        f"As `root`, with {n} more empty untagged containers directly under `/`, "
        "added before and removed after each run. Deep search looks up the ids "
        "of the root's hidden untagged children first and excludes them; above "
        "1,000 it uses a slower general condition instead. "
        + (
            "This is just under that limit."
            if n == 999
            else "This is just over that limit."
            if n == 1000
            else ""
        ),
        ROOT,
        False,
        "untagged_top",
        ["deep search", "search root is /", f"{n + 1} hidden untagged children of /"],
    )
tree(
    "admin.root",
    "Admin deep search from /",
    "No access filter: the cost of the query shape itself, for reference.",
    ROOT,
    True,
    "untagged_top",
    ["admin baseline"],
)

# --- inheritance.sql -----------------------------------------------------------------


def flat(key, tag, kind, children_note, children_kind):
    return under_root(
        node(key, tag, kind, node("n0..n199999", children_note, children_kind))
    )


tree(
    "inherit_true",
    "inherit_true: tagged children, container visible",
    "200,000 children tagged grp_0..grp_99, so 1% are visible, in a container "
    "the user can see.",
    flat("inherit_true", "bench_user", VISIBLE, "200k, grp_<i % 100>", PARTIAL),
    True,
    "inheritance",
    ["listing", "children tagged", "container's tags match", "1% visible"],
)
tree(
    "inherit_false",
    "inherit_false: tagged children, container hidden",
    "The same children in a container tagged `other`. Its untagged children "
    "would be hidden; it has none.",
    flat("inherit_false", "other", HIDDEN, "200k, grp_<i % 100>", PARTIAL),
    True,
    "inheritance",
    ["listing", "children tagged", "container's tags do not match"],
)
tree(
    "half_untagged",
    "half_untagged: half the children untagged",
    "Even children untagged (visible by inheritance), odd ones tagged grp_*.",
    flat("half_untagged", "bench_user", VISIBLE, "200k: 50% untagged", PARTIAL),
    False,
    "inheritance",
    ["listing", "children mixed", "about 50% visible"],
)
tree(
    "half_match",
    "half_match: the tagged twin of half_untagged",
    "The even children carry grp_0 instead of inheriting it: the nodes the "
    "user sees in half_untagged, but tagged, as needed without inheritance.",
    flat("half_match", "bench_user", VISIBLE, "200k: 50% grp_0", PARTIAL),
    True,
    "inheritance",
    ["listing", "tagged twin"],
)
tree(
    "admin.half_untagged",
    "Admin response for half_untagged",
    "No access filter: the cost of the query shape itself, for reference.",
    flat("half_untagged", "bench_user", VISIBLE, "200k: 50% untagged", PARTIAL),
    True,
    "inheritance",
    ["admin baseline"],
)
DEEP_TREE = under_root(
    node(
        "deep",
        "bench_user",
        VISIBLE,
        node(
            "level 1: 20",
            "every 10th tagged other, the rest untagged",
            PARTIAL,
            node("levels 2-4: 20 each", "odd grp_*, even untagged", PARTIAL),
        ),
    )
)
tree(
    "deep",
    "deep: a mixed 4-level tree",
    "4 levels of 20 children (168,420 nodes). Every 10th level-1 subtree is "
    "hidden by a tag the user lacks; elsewhere half the nodes are untagged.",
    DEEP_TREE,
    False,
    "inheritance",
    ["deep search", "descendants mixed", "10% of subtrees hidden"],
)
tree(
    "deep_tagged",
    "deep_tagged: the tagged twin of deep",
    "The untagged nodes of deep carry grp_0 instead.",
    DEEP_TREE,
    True,
    "inheritance",
    ["deep search", "tagged twin"],
)

# --- unique_keys.sql, deep_chain.sql, many_tags.sql ----------------------------------

CHAIN = under_root(
    node(
        "deepchain",
        "bench_user",
        VISIBLE,
        node(
            "c/c/.../c",
            "20 nested untagged containers, 10 leaves each",
            UNTAGGED,
            node("bottom c's children", "10,000 untagged", UNTAGGED),
        ),
    )
)
for suffix, same in [("", False), ("_tagged", True)]:
    twin = " (every node tagged grp_0: the tagged twin)" if suffix else ""
    tree(
        f"deepchain{suffix}_bottom",
        f"Listing the bottom of a 21-level chain{twin}",
        "The container 21 levels below its nearest tagged ancestor: its "
        "inherited tags come from far up." + twin,
        CHAIN,
        same,
        "deep_chain",
        ["listing", "nearest tagged ancestor 21 levels up"],
    )
    tree(
        f"deepchain{suffix}",
        f"Deep search through a 21-level chain{twin}",
        "Every candidate's path is up to 21 levels long." + twin,
        CHAIN,
        same,
        "deep_chain",
        ["deep search", "21 levels"],
    )
MANY = under_root(
    node(
        "many",
        "bench_user",
        VISIBLE,
        node("n0..n199999", "3 of 200 tags each; 1 in 5 untagged", PARTIAL),
    )
)
tree(
    "many",
    "many: many tags per user and per node",
    "The user holds 55 tags, each child 3 of 200; one in five children is untagged.",
    MANY,
    False,
    "many_tags",
    ["user holds 55 tags", "3 tags per node"],
)
tree(
    "many_tagged",
    "many_tagged: the tagged twin of many",
    "The untagged children carry mt_0, which the user holds.",
    MANY,
    True,
    "many_tags",
    ["user holds 55 tags", "tagged twin"],
)

# --- runs.sql ------------------------------------------------------------------------


def runs_shape(top, descendants_kind, descendants_note):
    return under_root(
        node(
            top,
            "bench_user",
            VISIBLE,
            node(
                "raw",
                "bench_user",
                VISIBLE,
                node(
                    "50,000 runs",
                    "grp_<i % 100>: 1% visible",
                    PARTIAL,
                    node(
                        "2 streams × 4 arrays",
                        descendants_note,
                        descendants_kind,
                    ),
                ),
            ),
        )
    )


RAW = runs_shape("tla", UNTAGGED, "untagged: inherit their run's tag")
RAW_TAGGED = runs_shape("tla_tagged", PARTIAL, "tagged like their run")
tree(
    "raw",
    "tla/raw: a beamline catalog, descendants inheriting",
    "The common NSLS-II layout: a public `raw` holding 50,000 runs, each "
    "tagged with its proposal's tag, of which the user sees 1%. The runs' "
    "streams and arrays have no tags of their own and inherit their run's.",
    RAW,
    None,
    "runs",
    ["deep search", "descendants inheriting", "1% of runs visible", "550k nodes"],
)
tree(
    "raw_tagged",
    "tla_tagged/raw: the same catalog, every node tagged",
    "As raw, but streams and arrays carry their run's tag, as today's "
    "catalogs must: the fair comparison with base.",
    RAW_TAGGED,
    True,
    "runs",
    ["deep search", "descendants tagged", "1% of runs visible", "550k nodes"],
)

# --- hidden_block.sql ----------------------------------------------------------------


def block_shape(key, fill_kind, fill_note):
    return under_root(
        node(
            key,
            "bench_user",
            VISIBLE,
            node(
                "l2",
                fill_note,
                fill_kind,
                node(
                    "l3",
                    fill_note,
                    fill_kind,
                    node("n0..n999", "grp_0", VISIBLE),
                    node(
                        "X",
                        "other",
                        HIDDEN,
                        node(
                            "500 × 1,000",
                            "500,500 nodes",
                            fill_kind if fill_kind == UNTAGGED else HIDDEN,
                        ),
                    ),
                    node("w0..w999", fill_note, fill_kind),
                ),
            ),
        )
    )


for key, kind, note, same in [
    ("block", UNTAGGED, "untagged", False),
    ("block_tagged", VISIBLE, "grp_0", True),
]:
    twin = " Every node is tagged: the fair comparison with base." if same else ""
    shape = block_shape(key, kind, note)
    tree(
        key,
        f"{key}: one large hidden subtree three levels down",
        "A hidden subtree X of 500,500 nodes, created between 1,000 visible "
        "nodes before it and 1,000 after it, so its ids form one block that a "
        "deep-search page must skip." + twin,
        shape,
        same,
        "hidden_block",
        ["deep search", "one hidden block below /", "500k hidden nodes"],
    )
    tree(
        f"{key}_l3",
        f"Listing l3 in {key}",
        "Listing the container that holds X: only X itself is hidden, its "
        "descendants are not children." + twin,
        shape,
        same,
        "hidden_block",
        ["listing", "2,001 children"],
    )

# --- grid.sql ------------------------------------------------------------------------

GRID_SHAPES = {
    "wide": ("10,000 runs", ["2 streams", "4 arrays each"], "110,001 nodes"),
    "deep": ("100 runs", ["10", "10", "10"], "111,101 nodes"),
}
GRID_VISIBLE = {
    "1pct": ("grp_<i % 100>: 1% visible", PARTIAL),
    "50pct": ("every other run grp_0: 50% visible", PARTIAL),
    "100pct": ("grp_0: all visible", VISIBLE),
    "inherit": ("untagged: inherit bench_user", UNTAGGED),
}
GRID_DESCENDANTS = {
    "tagged": ("tagged like their run", None),
    "mixed": ("odd i grp_0, even untagged", PARTIAL),
    "inheriting": ("untagged: inherit", UNTAGGED),
}


def grid_cells():
    "(cell, shape, visible, descendants, untagged search root) per grid tree."
    for shape in GRID_SHAPES:
        for visible in GRID_VISIBLE:
            for descendants in GRID_DESCENDANTS:
                if visible == "inherit" and descendants == "tagged":
                    continue
                yield (
                    f"{shape}_{visible}_{descendants}",
                    shape,
                    visible,
                    descendants,
                    False,
                )
    for descendants in GRID_DESCENDANTS:
        yield f"wide_1pct_{descendants}_uroot", "wide", "1pct", descendants, True


for cell, shape, visible, descendants, uroot in grid_cells():
    runs_label, levels, size = GRID_SHAPES[shape]
    runs_note, runs_kind = GRID_VISIBLE[visible]
    desc_note, desc_kind = GRID_DESCENDANTS[descendants]
    if desc_kind is None:
        desc_kind = runs_kind
    below = None
    for level in reversed(levels):
        children = [below] if below else []
        below = node(level, desc_note, desc_kind, *children)
    shape_tree = under_root(
        node(
            "grid",
            "bench_user",
            VISIBLE,
            node(
                cell,
                "untagged (inherits)" if uroot else "bench_user",
                UNTAGGED if uroot else VISIBLE,
                node(runs_label, runs_note, runs_kind, below),
            ),
        )
    )
    tree(
        f"grid.{cell}",
        f"grid/{cell}",
        f"A {shape} catalog of runs ({size}): runs {runs_note.split(': ')[-1]}; "
        f"descendants {desc_note}."
        + (" The search root has no tags of its own." if uroot else ""),
        shape_tree,
        descendants == "tagged" and visible != "inherit" and not uroot,
        "grid",
        [shape, f"runs {visible}", f"descendants {descendants}"]
        + (["untagged search root"] if uroot else []),
    )
    TREES[f"grid.{cell}"]["grid"] = dict(
        shape=shape, visible=visible, descendants=descendants, uroot=uroot
    )

# --- Coverage --------------------------------------------------------------------------

# (factor, value, trees that cover it). report.py links each tree to its
# section and warns about trees that have no results.
COVERAGE = [
    (
        "Listing: the container's tags",
        "match",
        ["inherit_true", "grid.wide_1pct_tagged"],
    ),
    ("Listing: the container's tags", "do not match", ["inherit_false"]),
    (
        "Listing: the container's tags",
        "none (inherited)",
        ["deepchain_bottom", "grid.wide_1pct_tagged_uroot"],
    ),
    (
        "Listing: children",
        "all tagged",
        ["inherit_true", "raw", "grid.wide_1pct_tagged"],
    ),
    ("Listing: children", "mixed", ["half_untagged", "P", "Q", "many"]),
    (
        "Listing: children",
        "all untagged (inheriting)",
        [
            "grid.wide_inherit_inheriting",
            "grid.deep_inherit_inheriting",
            "deepchain_bottom",
        ],
    ),
    (
        "Listing: fraction visible",
        "about 1%",
        ["Q", "raw", "inherit_true", "grid.wide_1pct_tagged"],
    ),
    (
        "Listing: fraction visible",
        "about 50%",
        ["P", "half_untagged", "grid.wide_50pct_tagged"],
    ),
    (
        "Listing: fraction visible",
        "100%",
        ["grid.wide_100pct_tagged", "grid.wide_inherit_inheriting"],
    ),
    ("Listing: width", "1M", ["P", "Q"]),
    ("Listing: width", "200k", ["inherit_true", "half_untagged", "many"]),
    ("Listing: width", "10k-50k", ["raw", "grid.wide_1pct_tagged", "deepchain_bottom"]),
    ("Listing: width", "100-2,000", ["grid.deep_1pct_tagged", "block_l3"]),
    ("Deep: search root", "/", ["root", "root+10_untagged"]),
    (
        "Deep: search root",
        "own tags match",
        ["raw", "deep", "block", "grid.wide_1pct_tagged"],
    ),
    ("Deep: search root", "own tags do not match", ["bl"]),
    (
        "Deep: search root",
        "untagged (inherits)",
        ["grid.wide_1pct_tagged_uroot", "grid.wide_1pct_inheriting_uroot"],
    ),
    (
        "Deep: descendants",
        "tagged",
        ["raw_tagged", "deep_tagged", "block_tagged", "grid.wide_1pct_tagged"],
    ),
    ("Deep: descendants", "mixed", ["deep", "grid.wide_1pct_mixed"]),
    (
        "Deep: descendants",
        "inheriting",
        ["raw", "block", "deepchain", "grid.wide_1pct_inheriting"],
    ),
    (
        "Deep: fraction visible",
        "1%",
        ["raw", "grid.wide_1pct_tagged", "grid.deep_1pct_tagged"],
    ),
    (
        "Deep: fraction visible",
        "50%",
        ["grid.wide_50pct_tagged", "grid.deep_50pct_tagged"],
    ),
    (
        "Deep: fraction visible",
        "90-100%",
        ["deep", "grid.wide_100pct_tagged", "grid.wide_inherit_inheriting"],
    ),
    ("Deep: shape", "wide (runs × streams × arrays)", ["raw", "grid.wide_1pct_tagged"]),
    ("Deep: shape", "deep (4 levels)", ["deep", "grid.deep_1pct_tagged"]),
    ("Deep: shape", "long chain (21 levels)", ["deepchain"]),
    (
        "Deep: hidden nodes laid out",
        "interleaved",
        ["raw", "Q", "grid.wide_1pct_tagged"],
    ),
    ("Deep: hidden nodes laid out", "one block below /", ["block"]),
    ("Deep: hidden nodes laid out", "one block at / (untagged child)", ["root"]),
    (
        "Deep: hidden at",
        "the run (whole subtrees)",
        ["raw", "grid.wide_1pct_inheriting"],
    ),
    ("Deep: hidden at", "the leaf", ["bl"]),
    (
        "Deep: hidden at",
        "visible tags inside a hidden subtree",
        ["deep", "grid.wide_1pct_mixed"],
    ),
    (
        "Deep: hidden untagged children of /",
        "1 / 11 / 1,000 / 1,001",
        ["root", "root+10_untagged", "root+999_untagged", "root+1000_untagged"],
    ),
    ("Tags", "user holds 55, nodes have 3", ["many", "many_tagged"]),
    (
        "Reference",
        "admin (no filter)",
        ["admin.P", "admin.half_untagged", "admin.root"],
    ),
]
