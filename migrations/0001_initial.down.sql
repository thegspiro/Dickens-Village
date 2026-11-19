-- Rollback for 0001_initial.
-- DESTRUCTIVE: drops every collection table and all data in them.
-- Back up first: docker compose exec db sh -c 'mysqldump -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' > backup.sql
-- Child tables are dropped before items because of their foreign keys.

DROP TABLE IF EXISTS item_photos;
DROP TABLE IF EXISTS item_links;
DROP TABLE IF EXISTS item_extras;
DROP TABLE IF EXISTS items;
