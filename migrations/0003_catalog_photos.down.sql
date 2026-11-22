-- Rollback for 0003_catalog_photos.
-- Drops the catalog photo records. The image files stay in UPLOAD_DIR and can be
-- deleted by hand; your own pieces' photos (item_photos) are not affected.

DROP TABLE IF EXISTS catalog_photos;
