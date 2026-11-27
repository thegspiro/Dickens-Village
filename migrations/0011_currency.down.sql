-- Rollback for 0011_currency: drops the settings table and items.currency.
--
-- Back up first (scripts/backup.sh). Amounts are kept as they were entered, but
-- which of them were Canadian dollars is lost: the older app shows every amount
-- as US dollars. The site currency and exchange rate are lost too.

SET @dv_sql = (
    SELECT IF(COUNT(*) > 0,
        'ALTER TABLE items DROP COLUMN currency',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'items' AND column_name = 'currency'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;

DROP TABLE IF EXISTS site_settings;
