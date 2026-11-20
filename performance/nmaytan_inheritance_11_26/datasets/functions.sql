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

-- A catalog of runs: a container `tree_key` under `parent_id`, tagged `tag`
-- (untagged if NULL), with levels[1] runs, each with levels[2] children,
-- each with levels[3], and so on. Every node gets color/i metadata.
--
-- `visible` tags the runs, so the user (who holds grp_0) sees:
--   '1pct'     grp_<i % 100>: 1% of the runs, every 100th
--   '50pct'    grp_0 on even runs, grp_<i % 100> on odd ones: every other run
--   '100pct'   grp_0 on every run
--   'inherit'  no tags: the runs inherit the container's tags
-- `descendants` tags the nodes below the runs:
--   'tagged'      each with its run's tags (what a catalog without
--                 inheritance needs)
--   'inheriting'  none: they inherit their run's tags
--   'mixed'       grp_0 on those with an odd i, the rest untagged. Under a
--                 hidden run, the grp_0 ones stay hidden with inheritance.
-- Returns the container's id.
CREATE FUNCTION pg_temp.catalog(parent_id int, tree_key text, tag text,
                                levels int[], visible text, descendants text)
RETURNS int AS $$
DECLARE
    top int := pg_temp.container(parent_id, tree_key, tag);
    level_ids int[] := ARRAY[top];
BEGIN
    FOR k IN 1..array_length(levels, 1) LOOP
        PERFORM pg_temp.children(level_ids, levels[k]);
        level_ids := ARRAY(SELECT id FROM nodes WHERE parent = ANY(level_ids));
    END LOOP;
    IF visible <> 'inherit' THEN
        INSERT INTO node_access_tags_association(node_id, tag_id)
        SELECT n.id, t.id
        FROM nodes n
        JOIN access_tags t ON t.name = CASE
            WHEN visible = '100pct'
              OR (visible = '50pct' AND ((n.metadata->>'i')::int) % 2 = 0)
            THEN 'grp_0'
            ELSE 'grp_' || ((n.metadata->>'i')::int) % 100 END
        WHERE n.parent = top;
    END IF;
    IF descendants = 'tagged' THEN
        INSERT INTO node_access_tags_association(node_id, tag_id)
        SELECT c.descendant, a.tag_id
        FROM nodes run
        JOIN nodes_closure c ON c.ancestor = run.id
        JOIN node_access_tags_association a ON a.node_id = run.id
        WHERE run.parent = top AND c.depth >= 1;
    ELSIF descendants = 'mixed' THEN
        INSERT INTO node_access_tags_association(node_id, tag_id)
        SELECT n.id, t.id
        FROM nodes_closure c
        JOIN nodes n ON n.id = c.descendant
        JOIN access_tags t ON t.name = 'grp_0'
        WHERE c.ancestor = top AND c.depth >= 2
          AND ((n.metadata->>'i')::int) % 2 = 1;
    END IF;
    RETURN top;
END $$ LANGUAGE plpgsql;

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
