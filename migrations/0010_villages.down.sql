-- Rollback for 0010_villages: drops the village columns.
--
-- Back up first (scripts/backup.sh). After rolling back, Holy Land pieces and
-- places can no longer be told apart from Dickens' Village ones: they all show
-- together, as one collection.
--
-- The old rule that a place's name is unique within its parent is put back
-- first. If both villages have a place with the same name in the same spot
-- (two top-level "Tub 1"s, say), that step fails with a duplicate-entry error
-- and nothing is changed: rename one of them, then roll back again.

SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE storage_places ADD UNIQUE KEY uq_storage_places_parent_name (parent_key, name)',
        'DO 0')
    FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = 'storage_places'
      AND index_name = 'uq_storage_places_parent_name'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

SET @dv_sql = (
    SELECT IF(COUNT(*) > 0,
        'ALTER TABLE storage_places DROP INDEX uq_storage_places_village_parent_name',
        'DO 0')
    FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = 'storage_places'
      AND index_name = 'uq_storage_places_village_parent_name'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

SET @dv_sql = (
    SELECT IF(COUNT(*) > 0,
        'ALTER TABLE storage_places DROP COLUMN village',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'storage_places' AND column_name = 'village'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

SET @dv_sql = (
    SELECT IF(COUNT(*) > 0,
        'ALTER TABLE items DROP INDEX idx_items_village, DROP COLUMN village',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'items' AND column_name = 'village'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

SET @dv_sql = (
    SELECT IF(COUNT(*) > 0,
        'ALTER TABLE catalog_items DROP INDEX idx_catalog_items_village, DROP COLUMN village',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'catalog_items' AND column_name = 'village'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;
