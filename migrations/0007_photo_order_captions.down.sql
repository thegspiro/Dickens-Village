-- Rollback for 0007_photo_order_captions.
-- DESTRUCTIVE for photo order and captions only: drops those two columns (and
-- their index) from item_photos and catalog_photos. The photos themselves,
-- and everything else, are kept; photos then show in upload order again.

SET @dv_sql = (
    SELECT IF(COUNT(*) > 0,
        'ALTER TABLE item_photos DROP KEY idx_item_photos_order, DROP COLUMN caption, DROP COLUMN sort_order',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'item_photos' AND column_name = 'sort_order'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

SET @dv_sql = (
    SELECT IF(COUNT(*) > 0,
        'ALTER TABLE catalog_photos DROP KEY idx_catalog_photos_order, DROP COLUMN caption, DROP COLUMN sort_order',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'catalog_photos' AND column_name = 'sort_order'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;
