#!/bin/sh
# Back up the database and the uploaded photos and scans of a Docker Compose
# install. Run it on the Docker host, from the folder with docker-compose.yml,
# while the app is running:
#
#   scripts/backup.sh                 # into ./backups
#   scripts/backup.sh /mnt/user/backups/dickens-village
#   KEEP=14 scripts/backup.sh DIR     # also delete all but the 14 newest backups in DIR
#
# Each run writes DIR/dickens-backup-YYYYMMDD-HHMMSS/ containing:
#   database.sql.gz   mysqldump of the app's database (one consistent snapshot)
#   uploads.tar.gz    every file in the upload folder
#   SHA256SUMS        checksums of the two files
# The backup is checked before the script reports success. A failed run leaves
# a folder ending in .partial, which is never counted as a backup.
#
# Settings (environment variables):
#   DOCKER_COMPOSE  the compose command (default: "docker compose")
#   KEEP            keep only this many newest backups in DIR (default: keep all)
#
# POSIX sh only. Backups contain your whole collection: they are created
# readable by you only.
set -eu

COMPOSE="${DOCKER_COMPOSE:-docker compose}"
dest="${1:-./backups}"
keep="${KEEP:-}"

if [ -n "$keep" ]; then
  case "$keep" in
    *[!0-9]*) echo "KEEP must be a whole number" >&2; exit 1 ;;
  esac
  keep=$(printf '%s\n' "$keep" | sed 's/^0*//')  # "007" -> 7 (not octal)
  if [ -z "$keep" ]; then echo "KEEP must be at least 1" >&2; exit 1; fi
fi

umask 077
mkdir -p "$dest"
stamp=$(date +%Y%m%d-%H%M%S)
name="dickens-backup-$stamp"
work="$dest/$name.partial"
if [ -e "$dest/$name" ] || [ -e "$work" ]; then
  echo "A backup named $name already exists in $dest; try again in a second." >&2
  exit 1
fi
mkdir "$work"

fail() {
  echo "Backup failed: $1" >&2
  echo "The incomplete backup was left in $work" >&2
  exit 1
}

# Run a command, gzip its output into a file, and fail if the command itself
# failed (POSIX sh has no pipefail, so its exit status is saved to a file).
capture() {
  out="$1"
  shift
  status_file="$work/.status"
  { "$@"; echo $? > "$status_file"; } | gzip -c > "$out" || return 1
  status=$(cat "$status_file" 2>/dev/null || echo 1)
  rm -f "$status_file"
  [ "$status" = 0 ]
}

echo "Backing up the database..."
# The password is passed to mysqldump in MYSQL_PWD inside the db container, so
# it never appears on a command line. --single-transaction gives one
# consistent snapshot without locking the app out.
# shellcheck disable=SC2016,SC2086  # $ expands inside the container; $COMPOSE may be two words
capture "$work/database.sql.gz" $COMPOSE exec -T db sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" exec mysqldump --single-transaction --routines --triggers --no-tablespaces --default-character-set=utf8mb4 -u"$MYSQL_USER" "$MYSQL_DATABASE"' \
  || fail "mysqldump did not finish (is the db container running?)"
gzip -t "$work/database.sql.gz" || fail "the database dump is damaged"
gzip -dc "$work/database.sql.gz" | tail -n 1 | grep -q "Dump completed" \
  || fail "the database dump is incomplete"

echo "Backing up the uploaded photos and scans..."
# shellcheck disable=SC2086
capture "$work/uploads.tar.gz" $COMPOSE exec -T app tar -C /data/uploads -cf - . \
  || fail "could not read every file in the upload folder (is the app container running?)"
gzip -t "$work/uploads.tar.gz" || fail "the uploads archive is damaged"
gzip -dc "$work/uploads.tar.gz" | tar -tf - >/dev/null || fail "the uploads archive can't be read"

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$work" && sha256sum database.sql.gz uploads.tar.gz > SHA256SUMS)
elif command -v shasum >/dev/null 2>&1; then
  (cd "$work" && shasum -a 256 database.sql.gz uploads.tar.gz > SHA256SUMS)
fi

mv "$work" "$dest/$name"
echo "Backup written to $dest/$name"

if [ -n "$keep" ]; then
  # Backup folder names sort by date. Only complete backups made by this
  # script are considered; .partial folders and anything else are left alone.
  old=$(for dir in "$dest"/dickens-backup-[0-9]*; do
           [ -d "$dir" ] || continue
           case "$dir" in *.partial) continue ;; esac
           printf '%s\n' "$dir"
         done | sort -r | tail -n +"$((keep + 1))")
  if [ -n "$old" ]; then
    echo "$old" | while IFS= read -r dir; do
      rm -rf -- "$dir"
      echo "Removed old backup $dir"
    done
  fi
fi
