-- Rollback for 0008_catalog_listed: drops the listed flag. Entries removed from
-- the CSVs then show in the lookup again (as they did before 0008); nothing
-- else is changed.

SET @dv_sql = (
    SELECT IF(COUNT(*) > 0,
        'ALTER TABLE catalog_items DROP COLUMN listed',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'catalog_items' AND column_name = 'listed'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;
