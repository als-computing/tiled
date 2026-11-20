"""Write an HTML report comparing two bench.py result files.

Usage:
    pixi exec -s matplotlib -s numpy -s graphviz python report.py \\
        BASE.json NEW.json -o report.html [--notes notes.html]

The report is one self-contained file: charts are embedded PNGs and tree
diagrams inline SVG. For each tree it shows its shape, what it represents
and, for each operation, what Tiled does, why it is measured and how the two
runs compare. `--notes` inserts an HTML fragment (findings, causes) at the
top of the summary. Descriptions come from descriptions.py.
"""

import argparse
import base64
import html
import io
import json
import math
import re
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import descriptions as d  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

DATASETS = [
    ("base", "Very large containers (base.sql)"),
    ("untagged_top", "Deep search from the catalog root (untagged_top.sql)"),
    ("inheritance", "Listing and deep-search shapes (inheritance.sql)"),
    ("deep_chain", "Long untagged chains (deep_chain.sql)"),
    ("many_tags", "Many tags (many_tags.sql)"),
    ("runs", "A beamline catalog (runs.sql)"),
    ("hidden_block", "A large hidden block below the root (hidden_block.sql)"),
    ("grid", "Every combination of shape, visibility and tagging (grid.sql)"),
]
# Ratio buckets: (upper bound, name, colour).
BUCKETS = [
    (0.9, "faster", "#2e7d32"),
    (1.1, "at parity", "#9e9e9e"),
    (1.5, "slower", "#ef6c00"),
    (math.inf, "much slower", "#c62828"),
]
KIND_STYLE = {
    d.VISIBLE: ("#c8e6c9", "#2e7d32", "solid"),
    d.HIDDEN: ("#ffcdd2", "#c62828", "solid"),
    d.UNTAGGED: ("#f5f5f5", "#616161", "dashed"),
    d.PARTIAL: ("#fff3cd", "#b8860b", "solid"),
    d.CATALOG_ROOT: ("#bbdefb", "#1565c0", "solid"),
}
# A regression worth listing in the summary.
MIN_RATIO, MIN_DELTA_MS = 1.2, 2.0


# --- Data ------------------------------------------------------------------------


def split(name):
    "(tree, op) of a scenario name; walks and writes are their own trees."
    if name.startswith(("walk.", "write.")):
        group, op = name.split(".", 1)
        return group, op
    return tuple(name.rsplit(".", 1))


def ok(record):
    return record is not None and not record.get("timeout")


def ratio(b, n):
    if ok(b) and ok(n):
        return n["median_ms"] / b["median_ms"]
    return None


def bucket(r):
    for bound, name, colour in BUCKETS:
        if r < bound:
            return name, colour
    raise AssertionError


def fmt_ms(record):
    if record is None:
        return "–"
    if record.get("timeout"):
        return "timeout"
    ms = record["median_ms"]
    if ms >= 1000:
        return f"{ms / 1000:.2f} s"
    if ms >= 10:
        return f"{ms:.0f} ms"
    return f"{ms:.1f} ms"


def results_status(tree, b, n):
    """(text, css class) comparing what the two runs found."""
    if not (ok(b) and ok(n)):
        return "–", ""

    def shown(r):
        return "404" if r["results"] == -1 else f"{r['results']:,}"

    if b["fingerprint"] == n["fingerprint"]:
        return shown(n), ""
    text = f"{shown(b)} → {shown(n)}"
    same = d.TREES.get(tree, {}).get("same_results")
    if same:
        return text + " (unexpected)", "bad"
    return text, "differs"


# --- Rendering helpers -----------------------------------------------------------


def esc(text):
    "Escape, then show `code` spans."
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", html.escape(text))


def png(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    data = base64.b64encode(buf.getvalue()).decode()
    return f'<img src="data:image/png;base64,{data}">'


def anchor(tree):
    return "t-" + re.sub(r"[^A-Za-z0-9]+", "-", tree)


_svg_cache = {}


def diagram(shape):
    "Inline SVG of a tree shape (descriptions.node tuples), drawn by Graphviz."
    key = repr(shape)
    if key in _svg_cache:
        return _svg_cache[key]
    lines = [
        "digraph G {",
        'rankdir=TB; nodesep=0.25; ranksep=0.3; bgcolor="transparent";',
        'node [shape=box, style="rounded,filled", fontname="Helvetica", '
        'fontsize=10, margin="0.12,0.05"];',
        'edge [color="#9e9e9e", arrowsize=0.5];',
    ]
    counter = iter(range(10_000))

    def add(n, parent=None):
        label, note, kind, children = n
        fill, colour, style = KIND_STYLE[kind]
        nid = f"n{next(counter)}"
        text = html.escape(label)
        if note:
            text += (
                f'<BR/><FONT POINT-SIZE="8" COLOR="#424242">{html.escape(note)}</FONT>'
            )
        lines.append(
            f'{nid} [label=<{text}>, fillcolor="{fill}", color="{colour}", '
            f'style="rounded,filled,{style}"];'
        )
        if parent:
            lines.append(f"{parent} -> {nid};")
        for child in children:
            add(child, nid)

    add(shape)
    lines.append("}")
    svg = subprocess.run(
        ["dot", "-Tsvg"],
        input="\n".join(lines),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    svg = svg[svg.index("<svg") :]
    _svg_cache[key] = svg
    return svg


def legend():
    items = "".join(
        f'<span class="chip" style="background:{KIND_STYLE[k][0]};'
        f'border:1px {KIND_STYLE[k][2]} {KIND_STYLE[k][1]}">{esc(text)}</span>'
        for k, text in d.KIND_LEGEND.items()
    )
    return f'<p class="legend">{items}</p>'


# --- Charts ----------------------------------------------------------------------


def ratio_bars(rows, title):
    "Horizontal bars of new/base per scenario, log scale."
    rows = [(name, r) for name, r in rows if r is not None]
    if not rows:
        return ""
    height = max(1.6, 0.22 * len(rows) + 0.8)
    fig, ax = plt.subplots(figsize=(8, height))
    names = [name for name, _ in rows][::-1]
    values = [r for _, r in rows][::-1]
    ax.barh(names, [math.log2(v) for v in values], color=[bucket(v)[1] for v in values])
    lim = max(1.0, max(abs(math.log2(v)) for v in values) * 1.1)
    ax.set_xlim(-lim, lim)
    ticks = [t for t in range(-6, 7) if abs(t) <= lim]
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{2**t:g}×" for t in ticks])
    ax.axvline(0, color="black", lw=0.8)
    ax.set_xlabel("branch time ÷ base time (log scale; left is faster)")
    ax.set_title(title)
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(axis="x", alpha=0.3)
    return png(fig)


def overview_scatter(base, new):
    fig, ax = plt.subplots(figsize=(9, 5))
    by_dataset = {}
    for name, n in new["scenarios"].items():
        b = base["scenarios"].get(name)
        r = ratio(b, n)
        if r is None:
            continue
        tree, _ = split(name)
        ds = d.TREES.get(tree, {}).get("dataset", tree)
        by_dataset.setdefault(ds, []).append((b["median_ms"], r))
    for ds, points in sorted(by_dataset.items()):
        xs, ys = zip(*points)
        ax.scatter(xs, ys, s=14, alpha=0.75, label=ds)
    ax.set_xscale("log")
    ax.set_yscale("log", base=2)
    ax.axhline(1, color="black", lw=0.8)
    ax.axhspan(1.1, 64, color="#ffebee", alpha=0.5, zorder=0)
    ax.set_ylim(1 / 16, 16)
    ax.yaxis.set_major_formatter(
        matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}×")
    )
    ax.set_xlabel("base time (ms, log scale)")
    ax.set_ylabel("branch ÷ base (log scale)")
    ax.set_title("Every scenario: how slow it was before, and how it changed")
    ax.legend(fontsize=8, ncol=2, loc="lower right")
    ax.grid(alpha=0.3)
    return png(fig)


def grid_heatmap(base, new, op):
    """Ratio heat maps for one operation: wide and deep shapes side by side."""
    visible = list(d.GRID_VISIBLE)
    descendants = list(d.GRID_DESCENDANTS)
    fig, axes = plt.subplots(
        1, 2, figsize=(11, 2.6), gridspec_kw={"width_ratios": [5, 4]}
    )
    any_value = False
    for ax, shape in zip(axes, d.GRID_SHAPES):
        cols = visible + (["1pct\nuntagged root"] if shape == "wide" else [])
        grid = np.full((len(descendants), len(cols)), np.nan)
        labels = [["" for _ in cols] for _ in descendants]
        for i, desc in enumerate(descendants):
            for j, vis in enumerate(cols):
                cell = (
                    f"{shape}_{vis}_{desc}"
                    if "\n" not in vis
                    else f"{shape}_1pct_{desc}_uroot"
                )
                name = f"grid.{cell}.{op}"
                b, n = base["scenarios"].get(name), new["scenarios"].get(name)
                r = ratio(b, n)
                if r is not None:
                    any_value = True
                    grid[i, j] = math.log2(r)
                    labels[i][j] = f"{r:.2f}×\n{fmt_ms(n)}"
                elif n is not None:
                    labels[i][j] = fmt_ms(n)
        ax.imshow(grid, cmap="RdYlGn_r", vmin=-2, vmax=2, aspect="auto")
        for i in range(len(descendants)):
            for j in range(len(cols)):
                ax.text(j, i, labels[i][j] or "–", ha="center", va="center", fontsize=8)
        ax.set_xticks(range(len(cols)))
        ax.set_xticklabels([c if "\n" in c else f"runs {c}" for c in cols], fontsize=8)
        ax.set_yticks(range(len(descendants)))
        ax.set_yticklabels([f"descendants\n{x}" for x in descendants], fontsize=8)
        ax.set_title(f"{shape} shape", fontsize=10)
    fig.suptitle(f"{op}: branch ÷ base, and branch time", fontsize=11)
    fig.tight_layout()
    return png(fig) if any_value else ""


def root_untagged_chart(base, new):
    sizes = [
        ("root", 1),
        ("root+10_untagged", 11),
        ("root+999_untagged", 1000),
        ("root+1000_untagged", 1001),
    ]
    ops = ["deep_count", "deep_keys_page", "deep_keys_page_mid"]
    fig, axes = plt.subplots(1, len(ops), figsize=(11, 3.2))
    for ax, op in zip(axes, ops):
        for run, style in [(base, "o--"), (new, "o-")]:
            xs, ys = [], []
            for tree, n in sizes:
                rec = run["scenarios"].get(f"{tree}.{op}")
                if ok(rec):
                    xs.append(n)
                    ys.append(rec["median_ms"])
            ax.plot(xs, ys, style, label=run["label"])
        ax.set_xscale("log")
        ax.set_xticks([1, 11, 1000, 1001])
        ax.set_xticklabels(["1", "11", "1000", "\n1001"])
        ax.axvline(1000.5, color="#c62828", lw=0.8, ls=":")
        ax.set_xlabel("hidden untagged children of /")
        ax.set_ylabel("ms")
        ax.set_title(op, fontsize=10)
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=8)
    fig.suptitle(
        "Deep search from /: cost of hidden untagged root children "
        "(dotted line: the 1,000-id limit)",
        fontsize=11,
    )
    fig.tight_layout()
    return png(fig)


def walk_chart(base, new):
    names = [n for n in new["scenarios"] if n.startswith("walk.")]
    if not names:
        return ""
    fig, ax = plt.subplots(figsize=(9, 0.22 * len(names) + 1))
    y = np.arange(len(names))
    for offset, run in [(-0.2, base), (0.2, new)]:
        values = [run["scenarios"].get(n) for n in names]
        ax.barh(
            y + offset,
            [v["median_ms"] if ok(v) else 0 for v in values],
            height=0.4,
            label=run["label"],
        )
    ax.set_yticks(y)
    ax.set_yticklabels([n[5:] for n in names], fontsize=7)
    ax.invert_yaxis()
    ax.set_xlabel("ms to resolve the path")
    ax.set_title("Path walks: what every request pays to reach its node")
    ax.legend(fontsize=8)
    ax.grid(axis="x", alpha=0.3)
    return png(fig)


# --- Sections --------------------------------------------------------------------


def scenario_table(base, new, names, describe):
    rows = []
    for name in names:
        tree, op = split(name)
        b, n = base["scenarios"].get(name), new["scenarios"].get(name)
        what, why = describe(name, tree, op, n or b)
        r = ratio(b, n)
        if r is None:
            ratio_cell = "–"
        else:
            label, colour = bucket(r)
            ratio_cell = (
                f'<span class="ratio" style="background:{colour}">{r:.2f}×</span>'
            )
        status, css = results_status(tree, b, n)
        rows.append(
            f"<tr><td><code>{esc(op)}</code></td><td>{esc(what)}"
            f'<div class="why">{esc(why)}</div></td>'
            f'<td class="num">{fmt_ms(b)}</td><td class="num">{fmt_ms(n)}</td>'
            f'<td class="num">{ratio_cell}</td><td class="num {css}">{status}</td></tr>'
        )
    return (
        '<table class="scen"><tr><th>operation</th><th>what Tiled does, and why '
        f"it is measured</th><th>{esc(base['label'])}</th><th>{esc(new['label'])}"
        "</th><th>ratio</th><th>results</th></tr>" + "".join(rows) + "</table>"
    )


def describe_op(name, tree, op, record):
    return d.OPS.get(op, (op, record.get("covers", "") if record else ""))


def tree_section(base, new, tree, names, compact=False):
    info = d.TREES[tree]
    same = {
        True: "Base and the branch should find the same nodes.",
        False: "Inheritance changes which nodes are visible, so result "
        "counts may differ from base.",
        None: "Depending on the operation, inheritance may change which "
        "nodes are visible.",
    }[info["same_results"]]
    chips = "".join(f'<span class="chip">{esc(f)}</span>' for f in info["factors"])
    return (
        f'<section class="tree" id="{anchor(tree)}"><h3>{esc(info["title"])}'
        f' <code class="label">{esc(tree)}</code></h3>'
        f'<div class="treehead"><div class="diagram">{diagram(info["shape"])}</div>'
        f'<div class="about"><p>{esc(info["description"])}</p>'
        f'<p class="note">{esc(same)}</p><p>{chips}</p></div></div>'
        + scenario_table(base, new, names, describe_op)
        + "</section>"
    )


def summary(base, new, notes):
    counts = {name: 0 for _, name, _ in BUCKETS}
    regressions, unexpected, timeouts = [], [], []
    for name, n in new["scenarios"].items():
        b = base["scenarios"].get(name)
        tree, op = split(name)
        if (b and b.get("timeout")) or n.get("timeout"):
            timeouts.append(name)
        r = ratio(b, n)
        if r is None:
            continue
        counts[bucket(r)[0]] += 1
        if r >= MIN_RATIO and n["median_ms"] - b["median_ms"] >= MIN_DELTA_MS:
            regressions.append((r, name, b, n))
        if results_status(tree, b, n)[1] == "bad":
            unexpected.append(name)
    total = sum(counts.values())
    tiles = "".join(
        f'<div class="tile" style="border-color:{colour}"><b>{counts[name]}</b>'
        f"<span>{name}</span></div>"
        for _, name, colour in BUCKETS
    )
    regressions.sort(reverse=True)
    reg_rows = "".join(
        f'<tr><td><a href="#{anchor(split(name)[0])}"><code>{esc(name)}</code></a>'
        f'</td><td class="num">{fmt_ms(b)}</td><td class="num">{fmt_ms(n)}</td>'
        f'<td class="num"><span class="ratio" style="background:{bucket(r)[1]}">'
        f"{r:.2f}×</span></td></tr>"
        for r, name, b, n in regressions
    )
    parts = [
        "<h2 id='summary'>Summary</h2>",
        f'<div class="tiles">{tiles}<div class="tile"><b>{total}</b>'
        "<span>scenarios compared</span></div></div>",
        "<p>Faster: under 0.9× of base. At parity: 0.9-1.1×. Slower: 1.1-1.5×. "
        "Much slower: 1.5× or more. Times are medians; differences of a few "
        "percent or under a millisecond are noise.</p>",
    ]
    if notes:
        parts.append(f'<div class="notes">{notes}</div>')
    parts.append(overview_scatter(base, new))
    parts.append(
        f"<h3>Regressions: {MIN_RATIO}× or more and at least {MIN_DELTA_MS:g} ms "
        f"slower ({len(regressions)})</h3>"
    )
    parts.append(
        f'<table class="scen"><tr><th>scenario</th><th>{esc(base["label"])}</th>'
        f"<th>{esc(new['label'])}</th><th>ratio</th></tr>{reg_rows}</table>"
        if regressions
        else "<p>None.</p>"
    )
    parts.append(
        "<h3>Unexpected result differences</h3><p>Scenarios on fully tagged "
        "trees, where both versions must find the same nodes, but did not: "
        + (", ".join(f"<code>{esc(x)}</code>" for x in unexpected) or "none")
        + ".</p>"
    )
    if timeouts:
        parts.append(
            "<h3>Timeouts</h3><p>"
            + ", ".join(f"<code>{esc(x)}</code>" for x in timeouts)
            + "</p>"
        )
    return "\n".join(parts)


def coverage(new):
    trees = {split(name)[0] for name in new["scenarios"]}
    rows = []
    for factor, value, covered in d.COVERAGE:
        links = ", ".join(
            f'<a href="#{anchor(t)}"><code>{esc(t)}</code></a>'
            if t in trees
            else f'<code class="missing">{esc(t)} (not run)</code>'
            for t in covered
        )
        rows.append(
            f"<tr><td>{esc(factor)}</td><td>{esc(value)}</td><td>{links}</td></tr>"
        )
    return (
        "<h2 id='coverage'>Coverage</h2><p>The factors that change the access "
        "control SQL, and the trees that measure each value. Every listed tree "
        "runs the operations shown in its section.</p>"
        '<table class="cov"><tr><th>factor</th><th>value</th><th>trees</th></tr>'
        + "".join(rows)
        + "</table>"
    )


CSS = """
body { font-family: Helvetica, Arial, sans-serif; margin: 0 auto; max-width: 1200px;
       padding: 0 24px 80px; color: #212121; line-height: 1.4; }
h1 { margin-top: 32px; } h2 { border-bottom: 2px solid #1565c0; padding-bottom: 4px;
       margin-top: 48px; } h3 { margin-bottom: 6px; }
code { background: #f1f3f4; padding: 0 3px; border-radius: 3px; font-size: 90%; }
code.label { color: #616161; font-weight: normal; }
code.missing { color: #c62828; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 16px; font-size: 14px; }
th, td { border-bottom: 1px solid #e0e0e0; padding: 5px 8px; text-align: left;
         vertical-align: top; }
th { background: #fafafa; }
td.num { text-align: right; white-space: nowrap; }
td.differs { color: #6a1b9a; } td.bad { color: #c62828; font-weight: bold; }
.why { color: #616161; font-size: 12px; margin-top: 2px; }
.ratio { color: white; padding: 1px 6px; border-radius: 10px; font-weight: bold; }
.chip { display: inline-block; background: #e3f2fd; border-radius: 10px;
        padding: 1px 8px; margin: 2px 4px 2px 0; font-size: 12px; }
.legend .chip { color: #212121; }
.tiles { display: flex; gap: 12px; margin: 12px 0; flex-wrap: wrap; }
.tile { border: 3px solid #9e9e9e; border-radius: 8px; padding: 8px 16px;
        min-width: 110px; text-align: center; }
.tile b { display: block; font-size: 28px; } .tile span { font-size: 13px; }
.notes { background: #fffde7; border-left: 4px solid #fbc02d; padding: 4px 16px;
         margin: 16px 0; }
.tree { margin: 28px 0; padding-top: 4px; border-top: 1px dashed #bdbdbd; }
.treehead { display: flex; gap: 24px; align-items: flex-start; }
.diagram { flex: 0 0 auto; max-width: 55%; overflow-x: auto; }
.diagram svg { max-width: 100%; height: auto; }
.about { flex: 1; } .note { color: #6a1b9a; font-size: 13px; }
img { max-width: 100%; }
.heat { display: grid; grid-template-columns: 1fr; gap: 8px; }
nav a { margin-right: 12px; }
"""


def main(args):
    base = json.loads(Path(args.base).read_text())
    new = json.loads(Path(args.new).read_text())
    notes = Path(args.notes).read_text() if args.notes else ""
    names = list(new["scenarios"])
    names += [n for n in base["scenarios"] if n not in names]
    by_tree = {}
    for name in names:
        by_tree.setdefault(split(name)[0], []).append(name)

    body = [
        "<h1>Access-tag inheritance: benchmark report</h1>",
        f"<p>Comparing <b>{esc(base['label'])}</b> "
        f"(<code>{esc(base.get('commit', ''))}</code>) with <b>{esc(new['label'])}"
        f"</b> (<code>{esc(new.get('commit', ''))}</code>) on PostgreSQL. Each time "
        f"is the median of {new.get('runs')} runs after a warm-up run, through "
        "Tiled's own request-path code with a stub access policy. "
        f"{esc(d.USER)}</p>",
        '<nav><a href="#summary">Summary</a><a href="#coverage">Coverage</a>'
        + "".join(f'<a href="#ds-{ds}">{ds}</a>' for ds, _ in DATASETS)
        + '<a href="#walks">walks</a><a href="#writes">writes</a></nav>',
        summary(base, new, notes),
        coverage(new),
        "<h2>How to read the tree diagrams</h2>",
        legend(),
        "<p>Each section shows one tree: its shape, what it represents, and one "
        "row per operation. <i>ratio</i> is the branch's time divided by "
        "base's. <i>results</i> is the number of keys found (or the count); "
        "<span style='color:#6a1b9a'>purple</span> where the versions found "
        "different nodes, as expected where inheritance makes untagged nodes "
        "visible, and <span style='color:#c62828'>red</span> where they should "
        "have found the same.</p>",
    ]
    for ds, title in DATASETS:
        trees = [t for t in by_tree if d.TREES.get(t, {}).get("dataset") == ds]
        if not trees:
            continue
        body.append(f"<h2 id='ds-{ds}'>{esc(title)}</h2>")
        rows = [
            (name, ratio(base["scenarios"].get(name), new["scenarios"].get(name)))
            for t in trees
            for name in by_tree[t]
        ]
        if ds == "grid":
            body.append(
                "<p>Each heat map shows one operation on every grid tree: rows are "
                "how the runs' descendants are tagged, columns how the runs "
                "themselves are tagged. Green is faster than base, red slower; "
                "each cell gives the ratio and the branch's time.</p>"
            )
            ops = list(d.LISTING_OPS) + list(d.DEEP_OPS)
            body.append(
                '<div class="heat">'
                + "".join(grid_heatmap(base, new, op) for op in ops)
                + "</div>"
            )
        else:
            body.append(ratio_bars(rows, f"{ds}: branch ÷ base per scenario"))
        if ds == "untagged_top":
            body.append(root_untagged_chart(base, new))
        for t in trees:
            body.append(tree_section(base, new, t, by_tree[t]))

    walks = by_tree.get("walk", [])
    body.append("<h2 id='walks'>Path walks</h2>")
    body.append(f"<p>{esc(d.WALK_OP[0])}. {esc(d.WALK_OP[1])}</p>")
    body.append(walk_chart(base, new))
    body.append(
        scenario_table(
            base,
            new,
            walks,
            lambda name, tree, op, rec: (
                rec.get("covers", "") if rec else "",
                d.WALK_OP[0],
            ),
        )
    )
    writes = by_tree.get("write", [])
    body.append(
        "<h2 id='writes'>Writes</h2><p>Creating, retagging and deleting "
        "nodes, which maintain the untagged-nodes table and the closure "
        "table.</p>"
    )
    body.append(
        scenario_table(
            base,
            new,
            writes,
            lambda name, tree, op, rec: (rec.get("covers", "") if rec else "", ""),
        )
    )
    page = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Access-tag inheritance benchmark report</title>"
        f"<style>{CSS}</style></head><body>" + "\n".join(body) + "</body></html>"
    )
    Path(args.output).write_text(page)
    print(f"wrote {args.output} ({len(page) / 1e6:.1f} MB, {len(names)} scenarios)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("base", help="bench.py --json output of the baseline")
    parser.add_argument("new", help="bench.py --json output to compare")
    parser.add_argument("-o", "--output", default="report.html")
    parser.add_argument("--notes", help="HTML fragment for the summary")
    main(parser.parse_args())
