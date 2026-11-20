-- One large hidden subtree a few levels down, between visible siblings.
--
--   block/                bench_user
--     l2/                 untagged
--       l3/               untagged
--         n0..n999        grp_0 (visible)
--         X               other: 500 children × 1,000 (500,500 nodes), untagged
--         w0..w999        untagged (visible: they inherit bench_user)
--   block_tagged/         the same tree with l2, l3 and w* tagged grp_0, and
--                         X's descendants tagged other: the fair comparison
--                         for the version without inheritance
--
-- Nodes are created in that order, so X's subtree is one block of ids. A
-- deep-search page that starts before X must skip all of it; the untagged
-- children of the catalog root (untagged_top.sql) are the same case at the
-- root, which deep search handles differently.
\set ON_ERROR_STOP 1

CREATE FUNCTION pg_temp.block(block_key text, fill_tag text) RETURNS void AS $$
DECLARE
    l3 int := pg_temp.container(
        pg_temp.container(pg_temp.container(0, block_key, 'bench_user'),
                          'l2', fill_tag),
        'l3', fill_tag);
    x int;
    child int;
BEGIN
    PERFORM pg_temp.children(ARRAY[l3], 1000);
    PERFORM pg_temp.tag(ARRAY(SELECT id FROM nodes WHERE parent = l3), 'grp_0');
    x := pg_temp.container(l3, 'X', 'other');
    PERFORM pg_temp.children(ARRAY[x], 500);
    -- One statement per child of X: a single 500,000-row insert crashed the
    -- server (signal 11) on the schema without inheritance.
    FOR child IN SELECT id FROM nodes WHERE parent = x ORDER BY id LOOP
        PERFORM pg_temp.children(ARRAY[child], 1000);
    END LOOP;
    INSERT INTO nodes(parent, key, structure_family, metadata, specs)
    SELECT l3, 'w' || g, 'container',
           jsonb_build_object(
               'color', CASE WHEN g % 2 = 0 THEN 'blue' ELSE 'red' END, 'i', g),
           '[]'
    FROM generate_series(0, 999) g;
    IF fill_tag IS NOT NULL THEN
        PERFORM pg_temp.tag(
            ARRAY(SELECT id FROM nodes WHERE parent = l3 AND key LIKE 'w%'),
            fill_tag);
        INSERT INTO node_access_tags_association(node_id, tag_id)
        SELECT c.descendant, t.id
        FROM nodes_closure c JOIN access_tags t ON t.name = 'other'
        WHERE c.ancestor = x AND c.depth >= 1;
    END IF;
END $$ LANGUAGE plpgsql;

SELECT pg_temp.block('block', NULL);
SELECT pg_temp.block('block_tagged', 'grp_0');
