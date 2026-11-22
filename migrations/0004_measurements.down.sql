-- Rollback for 0004_measurements.
-- DESTRUCTIVE for measurement data only: drops the six dimension columns from
-- items. Every other piece detail is kept.

SET @dv_sql = (
    SELECT IF(COUNT(*) > 0,
        'ALTER TABLE items
            DROP CHECK chk_items_measurements,
            DROP COLUMN height_in, DROP COLUMN width_in, DROP COLUMN depth_in,
            DROP COLUMN box_height_in, DROP COLUMN box_width_in, DROP COLUMN box_depth_in',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'items' AND column_name = 'height_in'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;
