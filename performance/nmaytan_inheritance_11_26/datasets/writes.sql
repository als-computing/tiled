-- Targets for the write benchmarks, which change the catalog.
--
--   writes/       bench_user   empty; bench.py creates and deletes nodes here,
--                              and removes them again after each run
--   writes_big/   bench_user   100,000 untagged children; bench.py changes the
--                              tags of writes_big itself and restores them
--
-- The write scenarios leave the catalog as they found it, but they do change
-- table statistics and dead-row counts, so run them after the read
-- scenarios (bench.py's default order).
\set ON_ERROR_STOP 1

SELECT pg_temp.container(0, 'writes', 'bench_user');
SELECT pg_temp.children(ARRAY[pg_temp.container(0, 'writes_big', 'bench_user')], 100000);
