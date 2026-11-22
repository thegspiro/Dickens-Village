-- 0004_measurements: piece and box dimensions in inches on collection items.
-- The six columns are added in one ALTER (atomic in MySQL 8), guarded by an
-- information_schema check so re-running is harmless.

SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE items
            ADD COLUMN height_in DECIMAL(6, 3) NULL AFTER storage_tray,
            ADD COLUMN width_in DECIMAL(6, 3) NULL AFTER height_in,
            ADD COLUMN depth_in DECIMAL(6, 3) NULL AFTER width_in,
            ADD COLUMN box_height_in DECIMAL(6, 3) NULL AFTER depth_in,
            ADD COLUMN box_width_in DECIMAL(6, 3) NULL AFTER box_height_in,
            ADD COLUMN box_depth_in DECIMAL(6, 3) NULL AFTER box_width_in,
            ADD CONSTRAINT chk_items_measurements CHECK (
                (height_in IS NULL OR height_in > 0) AND (width_in IS NULL OR width_in > 0)
                AND (depth_in IS NULL OR depth_in > 0) AND (box_height_in IS NULL OR box_height_in > 0)
                AND (box_width_in IS NULL OR box_width_in > 0) AND (box_depth_in IS NULL OR box_depth_in > 0))',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'items' AND column_name = 'height_in'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;
