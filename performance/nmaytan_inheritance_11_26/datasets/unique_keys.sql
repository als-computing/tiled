-- Path walks with realistic keys.
--
--   keyed/   bench_user   200,000 children keyed 'k' || md5(i): every third
--                         (i % 3 = 0) untagged, the rest tagged grp_<i % 100>
--
-- Unlike the n0, n1, ... keys of inheritance.sql, no key repeats across
-- containers, so PostgreSQL estimates a lookup by key as the one row it is.
-- bench.py walks to (keys computed the same way in Python):
--
--   i = 100100   tagged grp_0: visible in every version
--   i = 99999    untagged, inherits bench_user: visible with inheritance
--   i = 100001   tagged grp_1: not visible (not found)
\set ON_ERROR_STOP 1

DO $$
DECLARE
    c int := pg_temp.container(0, 'keyed', 'bench_user');
BEGIN
    INSERT INTO nodes(parent, key, structure_family, metadata, specs)
    SELECT c, 'k' || md5(g::text), 'container',
           jsonb_build_object(
               'color', CASE WHEN g % 2 = 0 THEN 'blue' ELSE 'red' END, 'i', g),
           '[]'
    FROM generate_series(0, 199999) g;
    PERFORM pg_temp.tag(
        ARRAY(SELECT id FROM nodes
              WHERE parent = c AND ((metadata->>'i')::int) % 3 <> 0),
        'grp_%s', 100);
END $$;
