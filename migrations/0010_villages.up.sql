-- 0010_villages: which village a piece, storage place or catalog entry belongs to,
-- so Dickens' Village and the Holy Land Village are kept apart.
--   items.village          : the piece's village. Every existing piece is Dickens' Village.
--   storage_places.village : each village has its own storage places and display
--                            spots. Every existing place is Dickens' Village. A place's
--                            name only has to be unique within its parent in the
--                            same village, so both villages can have a "Tub 1".
--   catalog_items.village  : the village a reference entry belongs to, or NULL for
--                            entries that suit every village (General Village
--                            Accessories). Set by the catalog import.
--
-- Adding a village later means adding it to these ENUMs and to
-- app/validation.py VILLAGES.
--
-- Idempotent: every change is guarded by an information_schema check.

SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE items
            ADD COLUMN village ENUM(''dickens'', ''holy-land'') NOT NULL DEFAULT ''dickens'' AFTER category,
            ADD KEY idx_items_village (village)',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'items' AND column_name = 'village'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE storage_places
            ADD COLUMN village ENUM(''dickens'', ''holy-land'') NOT NULL DEFAULT ''dickens'' AFTER id',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'storage_places' AND column_name = 'village'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

-- The new unique key is added before the old one is dropped, so names stay
-- unique throughout.
SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE storage_places ADD UNIQUE KEY uq_storage_places_village_parent_name (village, parent_key, name)',
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
        'ALTER TABLE storage_places DROP INDEX uq_storage_places_parent_name',
        'DO 0')
    FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = 'storage_places'
      AND index_name = 'uq_storage_places_parent_name'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE catalog_items
            ADD COLUMN village ENUM(''dickens'', ''holy-land'') NULL AFTER list_name,
            ADD KEY idx_catalog_items_village (village)',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'catalog_items' AND column_name = 'village'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;
