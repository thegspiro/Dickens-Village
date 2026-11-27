-- 0011_currency: site-wide settings, and the currency each piece's prices are in.
--   site_settings : name -> value pairs set on the Settings page. Known names:
--                   currency (USD or CAD, the currency the site shows amounts in),
--                   usd_cad_rate (Canadian dollars per US dollar, typed in) and
--                   usd_cad_rate_set_on (the date it was last changed).
--   items.currency : the currency price_paid and estimated_value were entered in.
--                   Every existing piece is USD. Amounts are never rewritten; they
--                   are converted only for display.
--
-- Idempotent: the table uses IF NOT EXISTS, the seed row INSERT IGNORE, and the
-- ALTER an information_schema check.

CREATE TABLE IF NOT EXISTS site_settings (
    name       VARCHAR(64) NOT NULL,
    value      VARCHAR(255) NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (name)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci;

INSERT IGNORE INTO site_settings (name, value) VALUES ('currency', 'USD');

SET @dv_sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE items ADD COLUMN currency ENUM(''USD'', ''CAD'') NOT NULL DEFAULT ''USD'' AFTER estimated_value',
        'DO 0')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'items' AND column_name = 'currency'
);
PREPARE dv_stmt FROM @dv_sql;
EXECUTE dv_stmt;
DEALLOCATE PREPARE dv_stmt;
