-- Rollback for 0002_catalog.
-- Removes the link from items to the catalog (existing items are kept; only the
-- catalog_id column is dropped) and drops the catalog table. The catalog can be
-- reloaded from data/catalog with: python -m app.catalog sync

SET @dv_sql = (
    SELECT IF(COUNT(*) > 0, 'ALTER TABLE items DROP FOREIGN KEY fk_items_catalog', 'DO 0')
    FROM information_schema.table_constraints
    WHERE table_schema = DATABASE() AND table_name = 'items'
      AND constraint_name = 'fk_items_catalog' AND constraint_type = 'FOREIGN KEY'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

SET @dv_sql = (
    SELECT IF(COUNT(*) > 0, 'ALTER TABLE items DROP COLUMN catalog_id', 'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'items' AND column_name = 'catalog_id'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

DROP TABLE IF EXISTS catalog_items;
