-- A wide catalog of runs, most of them hidden from any one user.
--
--   tla/              bench_user (readable by everyone, like a public tag)
--     raw/            bench_user
--       n0..n49999    50,000 runs tagged grp_<i % 100>; each has 2 streams
--                     with 4 arrays each, all untagged (they inherit the
--                     run's tag)
--   tla_tagged/raw/   the same tree with the streams and arrays tagged like
--                     their run: the fair comparison for the version without
--                     inheritance
--
-- The benchmark user holds grp_0, so it sees 1% of the runs, spread evenly
-- through the id order, and deep search from raw must reject the subtrees of
-- the other 99%. 550,000 nodes per tree. grid.sql has smaller trees of the
-- same kind in every combination of visibility and tagging.
\set ON_ERROR_STOP 1

-- pg_temp.container and .catalog come from functions.sql.

SELECT pg_temp.catalog(pg_temp.container(0, 'tla', 'bench_user'), 'raw',
                       'bench_user', ARRAY[50000, 2, 4], '1pct', 'inheriting');
SELECT pg_temp.catalog(pg_temp.container(0, 'tla_tagged', 'bench_user'), 'raw',
                       'bench_user', ARRAY[50000, 2, 4], '1pct', 'tagged');
