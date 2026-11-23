-- 0007_photo_order_captions: display order (the first photo is the cover) and a
-- caption for each photo of a piece and each catalog reference photo.
-- Each ALTER is guarded by an information_schema check, so re-running is
-- harmless. The backfill numbers existing photos by upload order, and only
-- touches pieces / catalog entries whose photos are all still at 0, so it can
-- never undo an order chosen in the app.

SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE item_photos
            ADD COLUMN sort_order INT UNSIGNED NOT NULL DEFAULT 0 AFTER original_name,
            ADD COLUMN caption VARCHAR(300) NULL AFTER sort_order,
            ADD KEY idx_item_photos_order (item_id, sort_order, id)',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'item_photos' AND column_name = 'sort_order'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE catalog_photos
            ADD COLUMN sort_order INT UNSIGNED NOT NULL DEFAULT 0 AFTER original_name,
            ADD COLUMN caption VARCHAR(300) NULL AFTER sort_order,
            ADD KEY idx_catalog_photos_order (catalog_id, sort_order, id)',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'catalog_photos' AND column_name = 'sort_order'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

UPDATE item_photos p
JOIN (
    SELECT id, ROW_NUMBER() OVER (PARTITION BY item_id ORDER BY created_at, id) - 1 AS position
    FROM item_photos
    WHERE item_id IN (SELECT item_id FROM (
        SELECT item_id FROM item_photos GROUP BY item_id HAVING MAX(sort_order) = 0
    ) AS unordered)
) AS numbered ON numbered.id = p.id
SET p.sort_order = numbered.position;

UPDATE catalog_photos p
JOIN (
    SELECT id, ROW_NUMBER() OVER (PARTITION BY catalog_id ORDER BY created_at, id) - 1 AS position
    FROM catalog_photos
    WHERE catalog_id IN (SELECT catalog_id FROM (
        SELECT catalog_id FROM catalog_photos GROUP BY catalog_id HAVING MAX(sort_order) = 0
    ) AS unordered)
) AS numbered ON numbered.id = p.id
SET p.sort_order = numbered.position;
