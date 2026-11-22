-- 0006_item_scans: 3D scans (glTF 2.0 files) attached to pieces in the collection.
-- Idempotent (IF NOT EXISTS). Files live in UPLOAD_DIR, not in the database.

CREATE TABLE IF NOT EXISTS item_scans (
    id            INT UNSIGNED NOT NULL AUTO_INCREMENT,
    item_id       INT UNSIGNED NOT NULL,
    filename      VARCHAR(64) NOT NULL,
    original_name VARCHAR(255) NULL,
    size_bytes    BIGINT UNSIGNED NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_item_scans_filename (filename),
    KEY idx_item_scans_item (item_id),
    CONSTRAINT fk_item_scans_item FOREIGN KEY (item_id) REFERENCES items (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci;
