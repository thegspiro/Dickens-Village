-- Rollback for 0005_sync. Drops only the sync bookkeeping columns
-- (version numbers and phone-assigned IDs); no collection data is lost.

SET @dv_sql = (
    SELECT IF(COUNT(*) > 0,
        'ALTER TABLE item_photos DROP INDEX uq_item_photos_client_uuid, DROP COLUMN client_uuid', 'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'item_photos' AND column_name = 'client_uuid'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

SET @dv_sql = (
    SELECT IF(COUNT(*) > 0,
        'ALTER TABLE item_extras DROP INDEX uq_item_extras_client_uuid, DROP COLUMN client_uuid, DROP COLUMN version',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'item_extras' AND column_name = 'version'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

SET @dv_sql = (
    SELECT IF(COUNT(*) > 0,
        'ALTER TABLE items DROP INDEX uq_items_client_uuid, DROP COLUMN client_uuid, DROP COLUMN version',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'items' AND column_name = 'version'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;
