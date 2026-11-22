# Dickens-Village

A self-hosted tracker for a Department 56 Dickens' Village collection: buildings, figures and accessories, where each piece is stored, its condition, the extras that go with it, and what it's worth.

## Features

- **Pieces** in three categories: Building, Figure / Person, Accessory / Decoration. Each piece records its item number, series, year introduced and year retired.
- **Storage:** Location (where the tub is kept), Tub and Tray for every piece. Each field suggests values you've already used. The **Storage** page lists every location, tub and tray with a piece count, and links to the pieces inside.
- **Current condition:** a grade (Mint → Damaged), condition notes, the date you last checked, and whether you have the original box and sleeve.
- **Extras:** any number per piece (light cord, bulb, sign…), each with its own quantity, condition and notes. The collection list can filter on "has extras."
- **Links** between a building and the figures and accessories that go with it.
- **Photos:** uploads are re-encoded to JPEG and their metadata, including GPS location, is removed.
- **Purchase and value:** date, price paid, where you bought it, estimated value. The **Summary** page shows the totals.
- **Catalog lookup:** the Add-a-piece form has a lookup box. Type an item number (`56.65005`, `65005` or `5665005` all work) or part of a name, then pick the piece. Name, item number, category, series and years fill in, and the piece is linked to its catalog entry so its page shows the original retail price. Pieces that share a number are listed separately, each with its name and which list it came from.
- Search, filters, sorting and paging on the collection list.

## Reference catalog

`data/catalog/dickens_village_2025.csv` holds Department 56's 2025 Dickens' Village checklists: 399 lighted pieces and 361 accessories, including the individual pieces of sets. It loads into the database on every start (`python -m app.catalog sync`). Loading is an upsert, so it is safe to repeat, and corrections to the CSV take effect on the next restart. Lighted pieces fill in as Building and accessories as Accessory; change the category on the form when a piece is a figure.

Problems in the source PDFs, such as a retirement year printed as 2917 or one item number used for two pieces, are listed in `data/catalog/dickens_village_2025_source_issues.txt`. The impossible years were left blank rather than guessed. `scripts/catalog/` has the scripts that built the CSV from the PDFs.

## Stack

Python 3.13 and Flask, with server-rendered HTML and no JavaScript. Data lives in MySQL 8.4 (PyMySQL driver, plain-SQL migrations). It runs under gunicorn in Docker. All configuration comes from environment variables.

## Install (Docker Compose: Linux, Unraid, or a cloud VM)

1. Copy the example config: `cp .env.example .env`
2. Fill in `APP_PORT`, `DB_PASSWORD`, `MYSQL_ROOT_PASSWORD`, `SECRET_KEY` and `ADMIN_USERNAME` in `.env`. Generate the secret key with:
   `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`
3. Build the image and create your password hash:
   ```sh
   docker compose build
   docker compose run --rm --no-deps app python -m app.hashpw
   ```
   Paste the printed `ADMIN_PASSWORD_HASH='…'` line into `.env`. Keep the single quotes, because the hash contains `$`.
4. Start the app: `docker compose up -d`. Then open `http://<server>:<APP_PORT>`.

On startup the app applies any pending database migrations, then starts the web server. The database is not published on any host port.

### Unraid

To keep data in appdata, set these in `.env`:
```
UPLOADS_PATH=/mnt/user/appdata/dickens-village/uploads
APP_UID=99
APP_GID=100
```
Create the folder and give it to `nobody:users` (`chown 99:100`), then rebuild with `docker compose up -d --build`. MySQL data stays in the `db_data` named volume. You can point it at appdata the same way if you want: edit the `db_data` volume in `docker-compose.yml`.

### HTTPS and reverse proxies

If you expose the app beyond your home network, put it behind a reverse proxy with HTTPS. Then set `COOKIE_SECURE=true` and `TRUSTED_PROXIES=1`. Login throttling is tracked per worker process, so add rate limiting at the proxy if the app is reachable from the internet.

### AWS

The same image runs on ECS or EC2. It needs MySQL 8.0.19 or newer (the catalog import uses the `INSERT … AS new` syntax). Point `DB_HOST`, `DB_NAME`, `DB_USER` and `DB_PASSWORD` at an RDS MySQL 8 instance, and mount persistent storage (e.g. EFS) at `/data/uploads`. Photos are stored on disk only. S3 storage is not implemented.

## Backups

```sh
docker compose exec db sh -c 'mysqldump -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' > dickens-backup.sql
```
Also back up the uploads volume or folder.

## Migrations

```sh
docker compose run --rm app python -m app.migrate status
docker compose run --rm app python -m app.migrate up
docker compose run --rm app python -m app.migrate down --yes   # rolls back the latest migration
```
Each migration is a pair of files in `migrations/`. Every up migration is idempotent. `0001_initial.down.sql` drops every table, so back up before running it.

## Tests

```sh
pip install -r requirements-dev.txt
python -m pytest                                          # unit tests, no database needed
node --test tests/js/catalog_lookup.test.js               # catalog lookup script (Node 18+)
docker compose -f docker-compose.test.yml run --rm tests  # everything, including MySQL integration tests
docker compose -f docker-compose.test.yml down -v
```
