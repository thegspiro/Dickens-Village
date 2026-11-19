-- 0001_initial: core collection schema.
-- Idempotent: every statement uses IF NOT EXISTS, so re-running is safe.
-- Statements are separated by a semicolon at the end of a line (see app/migrate.py).

CREATE TABLE IF NOT EXISTS items (
    id                   INT UNSIGNED NOT NULL AUTO_INCREMENT,
    category             ENUM('building', 'figure', 'accessory') NOT NULL,
    name                 VARCHAR(200) NOT NULL,
    item_number          VARCHAR(32) NULL,
    series               VARCHAR(120) NULL,
    year_introduced      SMALLINT UNSIGNED NULL,
    year_retired         SMALLINT UNSIGNED NULL,
    purchase_date        DATE NULL,
    price_paid           DECIMAL(10, 2) NULL,
    purchased_from       VARCHAR(200) NULL,
    estimated_value      DECIMAL(10, 2) NULL,
    condition_grade      ENUM('mint', 'excellent', 'good', 'fair', 'poor', 'damaged') NULL,
    condition_notes      TEXT NULL,
    condition_checked_on DATE NULL,
    has_original_box     TINYINT(1) NOT NULL DEFAULT 0,
    has_sleeve           TINYINT(1) NOT NULL DEFAULT 0,
    storage_location     VARCHAR(120) NULL,
    storage_tub          VARCHAR(60) NULL,
    storage_tray         VARCHAR(60) NULL,
    on_display           TINYINT(1) NOT NULL DEFAULT 0,
    notes                TEXT NULL,
    created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_items_category (category),
    KEY idx_items_name (name),
    KEY idx_items_item_number (item_number),
    KEY idx_items_storage (storage_location, storage_tub, storage_tray),
    CONSTRAINT chk_items_years CHECK (
        year_retired IS NULL OR year_introduced IS NULL OR year_retired >= year_introduced
    ),
    CONSTRAINT chk_items_price_paid CHECK (price_paid IS NULL OR price_paid >= 0),
    CONSTRAINT chk_items_estimated_value CHECK (estimated_value IS NULL OR estimated_value >= 0)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS item_extras (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    item_id         INT UNSIGNED NOT NULL,
    description     VARCHAR(200) NOT NULL,
    quantity        SMALLINT UNSIGNED NOT NULL DEFAULT 1,
    condition_grade ENUM('mint', 'excellent', 'good', 'fair', 'poor', 'damaged') NULL,
    notes           VARCHAR(1000) NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_item_extras_item (item_id),
    CONSTRAINT fk_item_extras_item FOREIGN KEY (item_id) REFERENCES items (id) ON DELETE CASCADE,
    CONSTRAINT chk_item_extras_quantity CHECK (quantity >= 1)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS item_links (
    building_id INT UNSIGNED NOT NULL,
    item_id     INT UNSIGNED NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (building_id, item_id),
    KEY idx_item_links_item (item_id),
    CONSTRAINT fk_item_links_building FOREIGN KEY (building_id) REFERENCES items (id) ON DELETE CASCADE,
    CONSTRAINT fk_item_links_item FOREIGN KEY (item_id) REFERENCES items (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS item_photos (
    id            INT UNSIGNED NOT NULL AUTO_INCREMENT,
    item_id       INT UNSIGNED NOT NULL,
    filename      VARCHAR(64) NOT NULL,
    original_name VARCHAR(255) NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_item_photos_filename (filename),
    KEY idx_item_photos_item (item_id),
    CONSTRAINT fk_item_photos_item FOREIGN KEY (item_id) REFERENCES items (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci;
