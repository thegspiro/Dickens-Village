-- 0009_storage_places: storage places as a tree (Location > Cabinet > Drawer > Spot,
-- or any other nesting), so a drawer or a spot can be labelled and looked up.
--   storage_places          : one row per place; parent_id NULL for a top-level place.
--                             kind is an optional type ("Drawer"), shown before a
--                             short name ("Drawer" + "3" reads "Drawer 3").
--                             is_display marks places where pieces are put on display.
--   items.storage_place_id  : where the piece is stored (its home).
--   items.display_place_id  : where it goes when it is on display.
--   item_extras.storage_place_id : where an extra is stored, when not with its piece.
--
-- Existing Location / Tub / Tray values become places, and every piece is pointed
-- at its place. The three text columns are kept: the app keeps them equal to the
-- first three levels of the piece's place, so exports and older phone app
-- versions keep working.
--
-- Idempotent: the table uses IF NOT EXISTS, each ALTER is guarded by an
-- information_schema check, places are inserted with INSERT IGNORE against a
-- unique key, and only pieces without a place yet are pointed at one.

CREATE TABLE IF NOT EXISTS storage_places (
    id         INT UNSIGNED NOT NULL AUTO_INCREMENT,
    parent_id  INT UNSIGNED NULL,
    -- 0 for top-level places, so the unique key below also covers them
    -- (a unique key treats every NULL as different).
    parent_key INT UNSIGNED AS (COALESCE(parent_id, 0)) STORED,
    name       VARCHAR(120) NOT NULL,
    kind       VARCHAR(40) NULL,
    is_display TINYINT(1) NOT NULL DEFAULT 0,
    notes      VARCHAR(1000) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_storage_places_parent_name (parent_key, name),
    KEY idx_storage_places_parent (parent_id),
    CONSTRAINT fk_storage_places_parent FOREIGN KEY (parent_id) REFERENCES storage_places (id) ON DELETE RESTRICT
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci;

SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE items
            ADD COLUMN storage_place_id INT UNSIGNED NULL AFTER storage_tray,
            ADD COLUMN display_place_id INT UNSIGNED NULL AFTER storage_place_id,
            ADD KEY idx_items_storage_place (storage_place_id),
            ADD KEY idx_items_display_place (display_place_id),
            ADD CONSTRAINT fk_items_storage_place FOREIGN KEY (storage_place_id)
                REFERENCES storage_places (id) ON DELETE RESTRICT,
            ADD CONSTRAINT fk_items_display_place FOREIGN KEY (display_place_id)
                REFERENCES storage_places (id) ON DELETE RESTRICT',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'items' AND column_name = 'storage_place_id'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE item_extras
            ADD COLUMN storage_place_id INT UNSIGNED NULL AFTER notes,
            ADD KEY idx_item_extras_storage_place (storage_place_id),
            ADD CONSTRAINT fk_item_extras_storage_place FOREIGN KEY (storage_place_id)
                REFERENCES storage_places (id) ON DELETE RESTRICT',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'item_extras' AND column_name = 'storage_place_id'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

-- Each piece's path is its non-blank Location, Tub and Tray values, in that
-- order. Tubs and trays get the type "Tub" / "Tray", so they read as before
-- ("Tub 3", "Tray Top"). Level 1:
INSERT IGNORE INTO storage_places (parent_id, name, kind)
SELECT NULL, d.n1, MIN(d.k1)
FROM (
    SELECT COALESCE(storage_location, storage_tub, storage_tray) AS n1,
           CASE WHEN storage_location IS NOT NULL THEN NULL
                WHEN storage_tub IS NOT NULL THEN 'Tub' ELSE 'Tray' END AS k1
    FROM items
    WHERE storage_place_id IS NULL
      AND (storage_location IS NOT NULL OR storage_tub IS NOT NULL OR storage_tray IS NOT NULL)
) AS d
GROUP BY d.n1;

-- Level 2:
INSERT IGNORE INTO storage_places (parent_id, name, kind)
SELECT p1.id, d.n2, MIN(d.k2)
FROM (
    SELECT COALESCE(storage_location, storage_tub, storage_tray) AS n1,
           CASE WHEN storage_location IS NOT NULL THEN COALESCE(storage_tub, storage_tray)
                WHEN storage_tub IS NOT NULL THEN storage_tray END AS n2,
           CASE WHEN storage_location IS NOT NULL AND storage_tub IS NOT NULL THEN 'Tub' ELSE 'Tray' END AS k2
    FROM items
    WHERE storage_place_id IS NULL
) AS d
JOIN storage_places p1 ON p1.parent_key = 0 AND p1.name = d.n1
WHERE d.n2 IS NOT NULL
GROUP BY p1.id, d.n2;

-- Level 3 (only pieces with all three values):
INSERT IGNORE INTO storage_places (parent_id, name, kind)
SELECT DISTINCT p2.id, i.storage_tray, 'Tray'
FROM items i
JOIN storage_places p1 ON p1.parent_key = 0 AND p1.name = i.storage_location
JOIN storage_places p2 ON p2.parent_key = p1.id AND p2.name = i.storage_tub
WHERE i.storage_place_id IS NULL
  AND i.storage_location IS NOT NULL AND i.storage_tub IS NOT NULL AND i.storage_tray IS NOT NULL;

-- Point each piece at the deepest place on its path.
UPDATE items i
JOIN storage_places p1
  ON p1.parent_key = 0 AND p1.name = COALESCE(i.storage_location, i.storage_tub, i.storage_tray)
LEFT JOIN storage_places p2
  ON p2.parent_key = p1.id
 AND p2.name = CASE WHEN i.storage_location IS NOT NULL THEN COALESCE(i.storage_tub, i.storage_tray)
                    WHEN i.storage_tub IS NOT NULL THEN i.storage_tray END
LEFT JOIN storage_places p3
  ON p3.parent_key = p2.id
 AND p3.name = CASE WHEN i.storage_location IS NOT NULL AND i.storage_tub IS NOT NULL THEN i.storage_tray END
SET i.storage_place_id = COALESCE(p3.id, p2.id, p1.id)
WHERE i.storage_place_id IS NULL;
