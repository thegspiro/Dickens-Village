-- 0002_catalog: reference catalog of Department 56 pieces, and an optional
-- link from a collection item to its catalog entry.
-- Idempotent: tables use IF NOT EXISTS; column, index and foreign key changes
-- check information_schema first because MySQL has no ADD COLUMN IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS catalog_items (
    id               INT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_key       VARCHAR(64) NOT NULL,
    list_name        VARCHAR(32) NOT NULL,
    item_number      VARCHAR(32) NULL,
    name             VARCHAR(200) NOT NULL,
    details          VARCHAR(1000) NULL,
    series           VARCHAR(120) NULL,
    year_issued      SMALLINT UNSIGNED NULL,
    year_retired     SMALLINT UNSIGNED NULL,
    is_current       TINYINT(1) NOT NULL DEFAULT 0,
    srp_usd          DECIMAL(10, 2) NULL,
    srp_cad          DECIMAL(10, 2) NULL,
    default_category ENUM('building', 'figure', 'accessory') NULL,
    parent_key       VARCHAR(64) NULL,
    source_page      SMALLINT UNSIGNED NULL,
    source_text      VARCHAR(1000) NULL,
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_catalog_items_source_key (source_key),
    KEY idx_catalog_items_item_number (item_number),
    KEY idx_catalog_items_name (name),
    KEY idx_catalog_items_parent_key (parent_key),
    CONSTRAINT chk_catalog_items_srp CHECK (
        (srp_usd IS NULL OR srp_usd >= 0) AND (srp_cad IS NULL OR srp_cad >= 0)
    )
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci;

SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE items ADD COLUMN catalog_id INT UNSIGNED NULL AFTER category',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'items' AND column_name = 'catalog_id'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE items ADD CONSTRAINT fk_items_catalog FOREIGN KEY (catalog_id) REFERENCES catalog_items (id) ON DELETE SET NULL',
        'DO 0')
    FROM information_schema.table_constraints
    WHERE table_schema = DATABASE() AND table_name = 'items'
      AND constraint_name = 'fk_items_catalog' AND constraint_type = 'FOREIGN KEY'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;
