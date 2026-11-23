-- 0008_catalog_listed: whether a catalog entry is still in the reference CSVs.
-- The catalog import sets it to 0 for entries that have been removed from the
-- CSVs instead of deleting them, so pieces linked to them and their reference
-- photos are kept; unlisted entries are hidden from the lookup.
-- Guarded by an information_schema check, so re-running is harmless.

SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE catalog_items ADD COLUMN listed TINYINT(1) NOT NULL DEFAULT 1 AFTER is_current',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'catalog_items' AND column_name = 'listed'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;
