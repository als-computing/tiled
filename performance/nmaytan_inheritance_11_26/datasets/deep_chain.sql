-- Long untagged chains, where the nearest tagged ancestor is far away.
--
--   deepchain/          bench_user
--     c/c/.../c         20 nested untagged containers; each level also has
--                       10 untagged leaves (leaf0..leaf9) beside the next c
--       (bottom c)      10,000 untagged children
--   deepchain_tagged/   the same tree with every node tagged grp_0: the fair
--                       comparison for the version without inheritance
--
-- With inheritance every node in deepchain/ inherits bench_user from 21
-- levels up, so path walks, listings at the bottom and effective tags all
-- look up a distant ancestor.
\set ON_ERROR_STOP 1

CREATE FUNCTION pg_temp.chain(container_key text, tag text) RETURNS void AS $$
DECLARE
    c int := pg_temp.container(0, container_key, 'bench_user');
BEGIN
    FOR _ IN 1..20 LOOP
        INSERT INTO nodes(parent, key, structure_family, metadata, specs)
        SELECT c, 'leaf' || g, 'container',
               jsonb_build_object('color', 'red', 'i', g), '[]'
        FROM generate_series(0, 9) g;
        c := pg_temp.container(c, 'c', NULL);
    END LOOP;
    PERFORM pg_temp.children(ARRAY[c], 10000);
    IF tag IS NOT NULL THEN
        PERFORM pg_temp.tag(
            ARRAY(SELECT cl.descendant FROM nodes_closure cl
                  JOIN nodes root ON root.id = cl.ancestor
                  WHERE root.parent = 0 AND root.key = container_key
                    AND cl.depth >= 1),
            tag);
    END IF;
END $$ LANGUAGE plpgsql;

SELECT pg_temp.chain('deepchain', NULL);
SELECT pg_temp.chain('deepchain_tagged', 'grp_0');
