-- Rollback for 0009_storage_places: drops the storage places and the columns
-- that point at them.
--
-- Location, Tub and Tray keep the first three levels of each piece's place,
-- because the app has kept them up to date, so the older app shows the same
-- storage. What is lost: places deeper than three levels, place types and
-- notes, display places, and where extras are stored. Back up first
-- (scripts/backup.sh).

SET @dv_sql = (
    SELECT IF(COUNT(*) > 0,
        'ALTER TABLE item_extras
            DROP FOREIGN KEY fk_item_extras_storage_place,
            DROP INDEX idx_item_extras_storage_place,
            DROP COLUMN storage_place_id',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'item_extras' AND column_name = 'storage_place_id'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

SET @dv_sql = (
    SELECT IF(COUNT(*) > 0,
        'ALTER TABLE items
            DROP FOREIGN KEY fk_items_storage_place,
            DROP FOREIGN KEY fk_items_display_place,
            DROP INDEX idx_items_storage_place,
            DROP INDEX idx_items_display_place,
            DROP COLUMN storage_place_id,
            DROP COLUMN display_place_id',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'items' AND column_name = 'storage_place_id'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

DROP TABLE IF EXISTS storage_places;
