-- Listing, metadata search, deep search and path walks around inheritance.
--
--   inherit_true/   bench_user   200,000 children tagged grp_<i % 100>
--   inherit_false/  other        200,000 children tagged grp_<i % 100>
--   half_untagged/  bench_user   200,000 children: even ones untagged, odd
--                                ones tagged grp_<i % 100>
--   half_match/     bench_user   like half_untagged, but the even children are
--                                tagged grp_0: what the user sees in
--                                half_untagged with inheritance, so the fair
--                                comparison for the version without it
--   deep/           bench_user   4 levels, 20 children per node (168,420
--                                nodes). Level 1: every 10th node tagged
--                                other (a hidden subtree), the rest untagged.
--                                Below: even nodes untagged, odd nodes tagged
--                                grp_<i % 100>
--   deep_tagged/    bench_user   like deep/, with grp_0 on the nodes deep/
--                                leaves untagged: the fair comparison for deep/
--   chain/c/c/c/c   bench_user   five nested containers, all tagged
--
-- The benchmark user holds bench_user and grp_0 (among others). Every child
-- has metadata {"color": "red"|"blue", "i": <index among its siblings>}.
-- Children are keyed n0, n1, ... in every container (see unique_keys.sql for
-- realistic keys).
\set ON_ERROR_STOP 1

INSERT INTO access_tags(name)
SELECT 'bench_user' UNION ALL SELECT 'other'
UNION ALL SELECT 'grp_' || g FROM generate_series(0, 99) g;

-- pg_temp.container, .children and .tag come from functions.sql.

CREATE FUNCTION pg_temp.flat(container_key text, tag text, untag_even bool,
                             fill_tag text)
RETURNS void AS $$
DECLARE
    c int := pg_temp.container(0, container_key, tag);
BEGIN
    PERFORM pg_temp.children(ARRAY[c], 200000);
    PERFORM pg_temp.tag(
        ARRAY(SELECT id FROM nodes
              WHERE parent = c
                AND (NOT untag_even OR ((metadata->>'i')::int) % 2 = 1)),
        'grp_%s', 100);
    IF fill_tag IS NOT NULL THEN
        PERFORM pg_temp.tag(
            ARRAY(SELECT id FROM nodes
                  WHERE parent = c AND ((metadata->>'i')::int) % 2 = 0),
            fill_tag);
    END IF;
END $$ LANGUAGE plpgsql;

CREATE FUNCTION pg_temp.tree(container_key text, fill_tag text) RETURNS void AS $$
DECLARE
    root int := pg_temp.container(0, container_key, 'bench_user');
BEGIN
    FOR level IN 1..4 LOOP
        PERFORM pg_temp.children(
            ARRAY(SELECT descendant FROM nodes_closure
                  WHERE ancestor = root AND depth = level - 1),
            20);
    END LOOP;
    -- Level 1: every 10th node hides its subtree; the rest are untagged.
    PERFORM pg_temp.tag(
        ARRAY(SELECT id FROM nodes
              WHERE parent = root AND ((metadata->>'i')::int) % 10 = 0),
        'other');
    -- Deeper levels: odd nodes tagged, even nodes untagged.
    PERFORM pg_temp.tag(
        ARRAY(SELECT n.id FROM nodes n
              JOIN nodes_closure c ON c.descendant = n.id
              WHERE c.ancestor = root AND c.depth >= 2
                AND ((n.metadata->>'i')::int) % 2 = 1),
        'grp_%s', 100);
    IF fill_tag IS NOT NULL THEN
        PERFORM pg_temp.tag(
            ARRAY(SELECT id FROM nodes
                  WHERE parent = root AND ((metadata->>'i')::int) % 10 <> 0),
            fill_tag);
        PERFORM pg_temp.tag(
            ARRAY(SELECT n.id FROM nodes n
                  JOIN nodes_closure c ON c.descendant = n.id
                  WHERE c.ancestor = root AND c.depth >= 2
                    AND ((n.metadata->>'i')::int) % 2 = 0),
            fill_tag);
    END IF;
END $$ LANGUAGE plpgsql;

SELECT pg_temp.flat('inherit_true', 'bench_user', false, NULL);
SELECT pg_temp.flat('inherit_false', 'other', false, NULL);
SELECT pg_temp.flat('half_untagged', 'bench_user', true, NULL);
SELECT pg_temp.flat('half_match', 'bench_user', true, 'grp_0');
SELECT pg_temp.tree('deep', NULL);
SELECT pg_temp.tree('deep_tagged', 'grp_0');

DO $$
DECLARE
    c int := pg_temp.container(0, 'chain', 'bench_user');
BEGIN
    FOR _ IN 1..4 LOOP
        c := pg_temp.container(c, 'c', 'bench_user');
    END LOOP;
END $$;
