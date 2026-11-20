# Performance benchmarks

Each subdirectory is a self-contained benchmark harness with its own README.

| Directory | What it measures |
|---|---|
| [`dylan_inheritance_11_26`](dylan_inheritance_11_26/README.md) | Access-tag inheritance (PR #1568): A/B timing and query plans of access-filtered catalog queries on PostgreSQL, base against branch |
| [`nmaytan_inheritance_11_26`](nmaytan_inheritance_11_26/README.md) | Access-tag inheritance on PostgreSQL: reads (listings, search, deep search, path walks, responses) and writes (triggers) over seven datasets, against the version before inheritance |
