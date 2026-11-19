# syntax=docker/dockerfile:1

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
ARG APP_UID=1000
ARG APP_GID=1000
# Reuse an existing group/user with these IDs if the base image has one
# (e.g. GID 100 "users" on Unraid-style setups).
RUN (getent group "$APP_GID" >/dev/null || groupadd --gid "$APP_GID" app) \
 && (getent passwd "$APP_UID" >/dev/null \
     || useradd --uid "$APP_UID" --gid "$APP_GID" --no-create-home --shell /usr/sbin/nologin app) \
 && mkdir -p /data/uploads \
 && chown -R "$APP_UID:$APP_GID" /data
COPY requirements.txt ./
RUN pip install -r requirements.txt

FROM base AS test
ARG APP_UID=1000
ARG APP_GID=1000
COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
COPY . .
USER ${APP_UID}:${APP_GID}
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider"]

FROM base AS runtime
ARG APP_UID=1000
ARG APP_GID=1000
COPY app ./app
COPY migrations ./migrations
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
ENV UPLOAD_DIR=/data/uploads
USER ${APP_UID}:${APP_GID}
EXPOSE 8000
VOLUME ["/data/uploads"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
