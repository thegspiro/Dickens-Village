-- 0005_sync: support offline editing from the phone app.
--   version     : bumped on every change, so a stale edit is detected instead of
--                 overwriting someone else's change
--   client_uuid : the ID a phone gives a new record, so a retried upload never
--                 creates a duplicate
-- Each table's columns are added in one guarded ALTER, so re-running is harmless.

SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE items
            ADD COLUMN version INT UNSIGNED NOT NULL DEFAULT 1 AFTER notes,
            ADD COLUMN client_uuid CHAR(36) NULL AFTER version,
            ADD UNIQUE KEY uq_items_client_uuid (client_uuid)',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'items' AND column_name = 'version'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE item_extras
            ADD COLUMN version INT UNSIGNED NOT NULL DEFAULT 1 AFTER notes,
            ADD COLUMN client_uuid CHAR(36) NULL AFTER version,
            ADD UNIQUE KEY uq_item_extras_client_uuid (client_uuid)',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'item_extras' AND column_name = 'version'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE item_photos
            ADD COLUMN client_uuid CHAR(36) NULL AFTER original_name,
            ADD UNIQUE KEY uq_item_photos_client_uuid (client_uuid)',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'item_photos' AND column_name = 'client_uuid'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;
