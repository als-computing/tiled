-- Users with many tags and nodes with several tags each.
--
--   many/          bench_user   200,000 children: every fifth (i % 5 = 0)
--                               untagged, the rest tagged with three of
--                               mt_0..mt_199: mt_<i % 200>, mt_<(i+67) % 200>
--                               and mt_<(i+134) % 200>
--   many_tagged/   bench_user   the same, with mt_0 on the nodes many/ leaves
--                               untagged: the fair comparison for many/
--
-- The many-tag scenarios in bench.py give the user mt_0..mt_49 (50 tags) on
-- top of the default ones.
\set ON_ERROR_STOP 1

INSERT INTO access_tags(name) SELECT 'mt_' || g FROM generate_series(0, 199) g;

CREATE FUNCTION pg_temp.many(container_key text, fill_tag text) RETURNS void AS $$
DECLARE
    c int := pg_temp.container(0, container_key, 'bench_user');
BEGIN
    PERFORM pg_temp.children(ARRAY[c], 200000);
    INSERT INTO node_access_tags_association(node_id, tag_id)
    SELECT n.id, t.id
    FROM nodes n,
         LATERAL (VALUES (0), (67), (134)) AS shift(s)
         JOIN access_tags t ON true
    WHERE n.parent = c
      AND ((n.metadata->>'i')::int) % 5 <> 0
      AND t.name = 'mt_' || ((((n.metadata->>'i')::int) + shift.s) % 200);
    IF fill_tag IS NOT NULL THEN
        PERFORM pg_temp.tag(
            ARRAY(SELECT id FROM nodes
                  WHERE parent = c AND ((metadata->>'i')::int) % 5 = 0),
            fill_tag);
    END IF;
END $$ LANGUAGE plpgsql;

SELECT pg_temp.many('many', NULL);
SELECT pg_temp.many('many_tagged', 'mt_0');
