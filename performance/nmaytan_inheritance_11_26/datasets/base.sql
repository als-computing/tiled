-- Large listings and deep search over a two-level tree.
--
--   /                 (the catalog root, created by tiled)
--   └── bl            bl_tag
--       ├── P         tagA      1,000,000 children: 50% tagA, 30% untagged, 20% tagB
--       └── Q         tagA      1,000,000 children: 98% tagC, 1% tagA, 1% untagged
--
-- The default benchmark user holds tagA but not bl_tag, tagB or tagC, so P's
-- and Q's untagged children are visible (they inherit tagA), and a deep
-- search from bl, whose own tag does not match, still finds them.
--
-- Node ids are fixed (bench.py's cursors depend on them): bl, P, Q are 1-3,
-- P's children 10.., Q's children 2,000,000... Load this dataset first.
\set ON_ERROR_STOP 1

INSERT INTO access_tags(name) VALUES
    ('bl_tag'), ('tagA'), ('tagB'), ('tagC'), ('user:alice');

INSERT INTO nodes(id, key, parent, structure_family, metadata, specs) VALUES
    (1, 'bl', 0, 'container', '{}', '[]'),
    (2, 'P', 1, 'container', '{}', '[]'),
    (3, 'Q', 1, 'container', '{}', '[]');

INSERT INTO node_access_tags_association(node_id, tag_id)
          SELECT 1, id FROM access_tags WHERE name = 'bl_tag'
UNION ALL SELECT 2, id FROM access_tags WHERE name = 'tagA'
UNION ALL SELECT 3, id FROM access_tags WHERE name = 'tagA';

INSERT INTO nodes(id, key, parent, structure_family, metadata, specs)
SELECT 10 + i, 'p' || i, 2, 'container', '{}', '[]' FROM generate_series(0, 999999) i;

INSERT INTO nodes(id, key, parent, structure_family, metadata, specs)
SELECT 2000000 + i, 'q' || i, 3, 'container', '{}', '[]' FROM generate_series(0, 999999) i;

-- P: i % 10 in 0..4 -> tagA, 5..7 -> untagged, 8..9 -> tagB
INSERT INTO node_access_tags_association(node_id, tag_id)
SELECT 10 + i, CASE WHEN i % 10 < 5 THEN a.id ELSE b.id END
FROM generate_series(0, 999999) i,
     (SELECT id FROM access_tags WHERE name = 'tagA') a,
     (SELECT id FROM access_tags WHERE name = 'tagB') b
WHERE i % 10 < 5 OR i % 10 >= 8;

-- Q: i % 100 = 0 -> tagA, = 1 -> untagged, else tagC
INSERT INTO node_access_tags_association(node_id, tag_id)
SELECT 2000000 + i, CASE WHEN i % 100 = 0 THEN a.id ELSE c.id END
FROM generate_series(0, 999999) i,
     (SELECT id FROM access_tags WHERE name = 'tagA') a,
     (SELECT id FROM access_tags WHERE name = 'tagC') c
WHERE i % 100 <> 1;
