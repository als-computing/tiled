-- Helpers shared by the dataset files. build.sh loads this file in the same
-- psql session before each dataset, so the pg_temp functions vanish with it.
\set ON_ERROR_STOP 1

-- A container under `parent_id`, tagged `tag` unless it is NULL.
CREATE FUNCTION pg_temp.container(parent_id int, child_key text, tag text)
RETURNS int AS $$
DECLARE
    new_id int;
BEGIN
    INSERT INTO nodes(parent, key, structure_family, metadata, specs)
    VALUES (parent_id, child_key, 'container', '{}', '[]')
    RETURNING id INTO new_id;
    IF tag IS NOT NULL THEN
        INSERT INTO node_access_tags_association(node_id, tag_id)
        SELECT new_id, id FROM access_tags WHERE name = tag;
    END IF;
    RETURN new_id;
END $$ LANGUAGE plpgsql;

-- `n` child containers of each of `parents`, keyed n0.. with color/i metadata.
CREATE FUNCTION pg_temp.children(parents int[], n int) RETURNS void AS $$
    INSERT INTO nodes(parent, key, structure_family, metadata, specs)
    SELECT p, 'n' || g, 'container',
           jsonb_build_object(
               'color', CASE WHEN g % 2 = 0 THEN 'blue' ELSE 'red' END, 'i', g),
           '[]'
    FROM unnest(parents) p, generate_series(0, n - 1) g;
$$ LANGUAGE sql;

-- Tag nodes: each of `ids` gets the tag named by `tag` (with %s replaced by
-- the node's `i`, or 0 if it has none, so 'grp_%s' with modulus 100 gives
-- grp_<i % 100>).
CREATE FUNCTION pg_temp.tag(ids int[], tag text, modulus int DEFAULT 1)
RETURNS void AS $$
    INSERT INTO node_access_tags_association(node_id, tag_id)
    SELECT n.id, t.id
    FROM nodes n
    JOIN access_tags t
      ON t.name = replace(
          tag, '%s', coalesce((((n.metadata->>'i')::int) % modulus)::text, '0'))
    WHERE n.id = ANY(ids);
$$ LANGUAGE sql;
