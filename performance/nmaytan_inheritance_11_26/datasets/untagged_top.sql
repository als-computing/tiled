-- A large untagged subtree directly under the catalog root.
--
--   /
--   └── U      untagged   500,000 untagged children
--
-- The catalog root's tags are not inherited, so with inheritance U and
-- everything below it are visible only to admins. A deep search from `/`
-- must exclude the whole subtree, and a walk into it must not find anything.
--
-- Node ids are fixed: U is 4, its children 5,000,000.. (bench.py pages from
-- inside this range). Load after base.sql.
\set ON_ERROR_STOP 1

INSERT INTO nodes(id, key, parent, structure_family, metadata, specs)
VALUES (4, 'U', 0, 'container', '{}', '[]');

INSERT INTO nodes(id, key, parent, structure_family, metadata, specs)
SELECT 5000000 + i, 'u' || i, 4, 'container', '{}', '[]'
FROM generate_series(0, 499999) i;

-- Later datasets take their ids from the sequence, above these.
SELECT setval(pg_get_serial_sequence('nodes', 'id'), 6000000);
