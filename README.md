# Dickens-Village

A self-hosted tracker for a Department 56 Dickens' Village collection: buildings, figures and accessories, where each piece is stored, its condition, the extras that go with it, and what it's worth.

## Features

- **Pieces** in three categories: Building, Figure / Person, Accessory / Decoration. Each piece records its item number, series, year introduced and year retired.
- **Storage:** Location (where the tub is kept), Tub and Tray for every piece. Each field suggests values you've already used. The **Storage** page lists every location, tub and tray with a piece count, and links to the pieces inside.
- **Current condition:** a grade (Mint → Damaged), condition notes, the date you last checked, and whether you have the original box and sleeve.
- **Extras:** any number per piece (light cord, bulb, sign…), each with its own quantity, condition and notes. The collection list can filter on "has extras."
- **Links** between a building and the figures and accessories that go with it.
- **Photos:** upload several at once, for your own pieces and as reference photos on catalog entries. Uploads are re-encoded to JPEG with their metadata (including GPS location) removed, and a small thumbnail is saved alongside. Catalog photos show in the lookup and on every piece linked to that entry.
- **Gallery:** photos show whole (never cropped). Tap one to open the full-screen viewer: swipe, use the arrow keys or the ‹ › buttons to move between photos, tap the photo to zoom, and download it from there. Give any photo a caption, and choose the order: drag photos on a computer, or use each photo's **Edit** buttons (Make cover, Earlier, Later). The first photo is the cover, shown in the collection list (and, for catalog photos, in the lookup).
- **3D scans:** attach GLB scans (from Polycam, Scaniverse, Luma and other apps) to a piece, then turn and zoom them in the page. Each file is checked to be a real glTF 2.0 model before it is stored. The viewer ([`<model-viewer>`](https://modelviewer.dev)) and its decoders are served by the app itself. The Docker image fetches them at build time; outside Docker, run `scripts/fetch_vendor.sh` once, which needs npm. Without them you can still upload and download scans. The phone app shows that a piece has scans but doesn't store them offline.
- **Measurements:** height, width and depth of each piece and of its box, in inches. Decimals or fractions both work (7.25, 7 1/4, 7¼), and eighths and sixteenths are kept exactly.
- **Purchase and value:** date, price paid, where you bought it, estimated value. The **Summary** page shows the totals.
- **Catalog lookup:** the Add-a-piece form has a lookup box. Type an item number (`56.65005`, `65005` or `5665005` all work) or part of a name, then pick the piece. Name, item number, category, series and years fill in, and the piece is linked to its catalog entry so its page shows the original retail price. Pieces that share a number are listed separately, each with its name and which list it came from.
- Search, filters, sorting and paging on the collection list.

## Reference catalog

`data/catalog/dickens_village_2025.csv` holds Department 56's 2025 Dickens' Village checklists: 399 lighted pieces and 361 accessories, including the individual pieces of sets. Browse it from the **Catalog** tab, where each entry has its own page with your reference photos, the pieces you own that are linked to it, and an "Add to my collection" button. It loads into the database on every start (`python -m app.catalog sync`). Loading is an upsert, so it is safe to repeat, and corrections to the CSV take effect on the next restart. Lighted pieces fill in as Building and accessories as Accessory; change the category on the form when a piece is a figure.

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

### Using it on your phone

The tracker is an installable web app (PWA). Open it in your phone's browser over **HTTPS**, then:

- **iPhone (Safari):** Share → *Add to Home Screen*.
- **Android (Chrome):** menu → *Install app* (or *Add to Home screen*).

It opens full-screen with its own icon. Phones only allow installing, and the service worker only runs, on an `https://` address, so serve it through your reverse proxy (see below). Over plain `http://` the home-screen icon still works, but as a shortcut to the website rather than an installed app.

The pages adapt to small screens: the menu collapses, lists become cards, and photo forms have a **Take a photo** button that opens the camera.

#### Working offline

The **Phone app** (menu → *Phone app*, or `/app`) keeps a copy of your collection and the catalog on the phone. If the server can't be reached, any page you open goes to the phone app instead. Offline you can:

- browse and search your pieces;
- add pieces, including filling them in from the catalog;
- edit pieces and extras, and add extras;
- take or attach photos (they're shrunk to 2000 pixels on the long side, the size the server keeps, so they take less space on the phone and upload faster). A photo the server refused can be tried again from **Needs attention**.

Changes are saved on the phone, and the pill at the top shows how many are waiting. They sync automatically when you're back online; tap the pill to sync right away. Browsing the website online also keeps the phone's copy up to date.

If the same field was changed on the phone and somewhere else, that piece is held and listed under **Needs attention**. There you choose which value to keep for each field. Changes to different fields are combined automatically. Deleting pieces and managing links between buildings and figures stay on the full site.

Logins last 30 days. If yours expires while you're offline, your changes stay on the phone and sync after you log in again. **Logging out erases the phone's copy**, and the app warns you first if anything hasn't synced. Logging out needs a connection; offline, nothing is erased. The bottom of the phone app's list has its own **Log out** button and **Erase this phone's copy**, which keeps you logged in.

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

GitHub Actions (`.github/workflows/ci.yml`) runs on every pull request and every push to `main`. It runs the Python tests against a real MySQL 8.4 (and fails if the integration tests were skipped), validates the catalog CSV, starts the app against MySQL and drives it with Playwright (desktop and phone-sized browsers, including offline editing and sync), runs the JavaScript tests, and builds the Docker images. The browser tests need the server's `MAX_UPLOAD_MB` set to 2. To run the same checks locally:

```sh
pip install -r requirements-dev.txt
python -m pytest                                          # unit tests, no database needed
node --test tests/js/catalog_lookup.test.js tests/js/offline_model.test.mjs   # browser scripts (Node 18+)
# Browser end-to-end tests (Playwright) against a running server with a throwaway database:
pip install -r requirements-e2e.txt && python -m playwright install chromium
E2E_BASE_URL=http://localhost:8000 E2E_USERNAME=admin E2E_PASSWORD=... python -m pytest tests/e2e
docker compose -f docker-compose.test.yml run --rm tests  # everything, including MySQL integration tests
docker compose -f docker-compose.test.yml down -v
```
