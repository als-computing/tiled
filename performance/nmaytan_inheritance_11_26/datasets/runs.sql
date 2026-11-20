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
-- the other 99%. 550,000 nodes per tree.
\set ON_ERROR_STOP 1

-- pg_temp.container, .children and .tag come from functions.sql.

CREATE FUNCTION pg_temp.runs(tla_key text, tag_descendants bool)
RETURNS void AS $$
DECLARE
    raw int := pg_temp.container(
        pg_temp.container(0, tla_key, 'bench_user'), 'raw', 'bench_user');
    runs int[];
BEGIN
    PERFORM pg_temp.children(ARRAY[raw], 50000);
    runs := ARRAY(SELECT id FROM nodes WHERE parent = raw);
    PERFORM pg_temp.tag(runs, 'grp_%s', 100);
    PERFORM pg_temp.children(runs, 2);
    PERFORM pg_temp.children(
        ARRAY(SELECT id FROM nodes WHERE parent = ANY(runs)), 4);
    IF tag_descendants THEN
        INSERT INTO node_access_tags_association(node_id, tag_id)
        SELECT c.descendant, a.tag_id
        FROM nodes_closure c
        JOIN node_access_tags_association a ON a.node_id = c.ancestor
        WHERE c.ancestor = ANY(runs) AND c.depth >= 1;
    END IF;
END $$ LANGUAGE plpgsql;

SELECT pg_temp.runs('tla', false);
SELECT pg_temp.runs('tla_tagged', true);
