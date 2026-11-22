-- 0003_catalog_photos: your own reference photos for catalog entries.
-- Idempotent (IF NOT EXISTS). Image files live in UPLOAD_DIR, not in the database.

CREATE TABLE IF NOT EXISTS catalog_photos (
    id            INT UNSIGNED NOT NULL AUTO_INCREMENT,
    catalog_id    INT UNSIGNED NOT NULL,
    filename      VARCHAR(64) NOT NULL,
    original_name VARCHAR(255) NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_catalog_photos_filename (filename),
    KEY idx_catalog_photos_catalog (catalog_id),
    CONSTRAINT fk_catalog_photos_catalog FOREIGN KEY (catalog_id) REFERENCES catalog_items (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci;
