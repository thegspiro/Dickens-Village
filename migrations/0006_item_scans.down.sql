-- Rollback for 0006_item_scans.
-- Drops the scan records. The scan files stay in UPLOAD_DIR and can be deleted by
-- hand (*.glb / *.gltf); nothing else is affected.

DROP TABLE IF EXISTS item_scans;
