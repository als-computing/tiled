-- Catalogs of runs in every combination of the factors that change the
-- access-control SQL, so that no combination goes unmeasured.
--
--   grid/                          bench_user
--     <shape>_<visible>_<descendants>/   bench_user
--     wide_1pct_<descendants>_uroot/     untagged (inherits bench_user)
--
--   shape        wide: 10,000 runs × 2 streams × 4 arrays (110,001 nodes)
--                deep: 100 runs × 10 × 10 × 10 (111,101 nodes)
--   visible      1pct, 50pct, 100pct of the runs visible, or inherit (runs
--                untagged)
--   descendants  tagged, mixed or inheriting (see pg_temp.catalog in
--                functions.sql); runs that are untagged have no tags to copy,
--                so inherit has no tagged variant
--
-- The _uroot trees are searched from a container with no tags of its own.
-- 25 trees, about 2.8M nodes. Load after inheritance.sql (grp_* tags).
\set ON_ERROR_STOP 1

DO $$
DECLARE
    grid int := pg_temp.container(0, 'grid', 'bench_user');
    shape text;
    levels int[];
    visible text;
    descendants text;
BEGIN
    FOR shape, levels IN
        VALUES ('wide', ARRAY[10000, 2, 4]), ('deep', ARRAY[100, 10, 10, 10])
    LOOP
        FOREACH visible IN ARRAY ARRAY['1pct', '50pct', '100pct', 'inherit'] LOOP
            FOREACH descendants IN ARRAY ARRAY['tagged', 'mixed', 'inheriting'] LOOP
                CONTINUE WHEN visible = 'inherit' AND descendants = 'tagged';
                PERFORM pg_temp.catalog(
                    grid, shape || '_' || visible || '_' || descendants,
                    'bench_user', levels, visible, descendants);
            END LOOP;
        END LOOP;
    END LOOP;
    FOREACH descendants IN ARRAY ARRAY['tagged', 'mixed', 'inheriting'] LOOP
        PERFORM pg_temp.catalog(
            grid, 'wide_1pct_' || descendants || '_uroot', NULL,
            ARRAY[10000, 2, 4], '1pct', descendants);
    END LOOP;
END $$;
