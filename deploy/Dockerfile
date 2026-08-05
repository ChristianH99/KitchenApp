# The kitchen app, as one container.
#
# Two stages: the first installs the dependencies, the second copies in the
# resulting virtualenv and the code. It halves the image — the build tools,
# uv itself and the pip cache never reach the runtime layer — and it means a
# code change rebuilds only the small stage.
#
# The DS723+ is an AMD64 Ryzen, so no cross-building is needed; `docker build`
# on the NAS, or on any x86-64 machine, produces the same image.

# ---------------------------------------------------------------------------
# Stage 1: dependencies
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/venv

WORKDIR /build

# The lockfile alone first, so this layer is cached until a dependency actually
# changes. Copying the whole source here would rebuild the world on every edit.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.13-slim

# gettext is needed at *build* time for compilemessages, and libjpeg/zlib at run
# time for Pillow. `--no-install-recommends` keeps this to what is named.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gettext \
        curl \
    && rm -rf /var/lib/apt/lists/*

# A non-root user, and one whose uid is easy to grant on a Synology share: the
# bind mount at /data has to be writable by whoever this process is, and
# "chown 1000" is a sentence the DSM file manager can carry out.
RUN groupadd --gid 1000 kitchen \
    && useradd --uid 1000 --gid 1000 --create-home kitchen

# Which build this is. Supplied by .github/workflows/release.yml from the tag;
# left as "dev" for a hand-built image, which is the honest answer — an image
# built from an uncommitted working copy has no version, and printing one that
# looks official is worse than printing nothing.
#
# It is baked as an environment variable rather than only as a label because the
# app itself shows it (the sidebar footer): `docker inspect` answers "what did I
# build", and the page answers "what am I actually looking at", which after a
# failed update are different questions.
ARG KITCHEN_VERSION=dev
ARG BUILD_REVISION=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="Kitchen" \
      org.opencontainers.image.description="A self-hosted recipe app for one household" \
      org.opencontainers.image.source="https://github.com/ChristianH99/KitchenApp" \
      org.opencontainers.image.version="${KITCHEN_VERSION}" \
      org.opencontainers.image.revision="${BUILD_REVISION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="NOASSERTION"

ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    KITCHEN_DATA_DIR=/data \
    KITCHEN_VERSION=${KITCHEN_VERSION}

COPY --from=builder /venv /venv

WORKDIR /app
COPY --chown=kitchen:kitchen . /app

# Both of these are *build* steps, not start-up steps, and doing them here is
# deliberate:
#
#   collectstatic — STORAGES uses WhiteNoise's manifest storage, so {% static %}
#   resolves through staticfiles.json. Without it every page renders unstyled
#   with a dead script tag. Running it at start-up instead would mean writing to
#   the image on every boot and a slower start for no gain.
#
#   compilemessages — nothing at runtime compiles a .po. The .mo files are
#   committed, but recompiling here means a container built from a checkout that
#   forgot to is still correct.
#
# DJANGO_SECRET_KEY is supplied because settings refuses to import without one
# when DEBUG is off; it is a throwaway used by this command only and never
# reaches the running app, which gets the real one from the environment.
RUN DJANGO_DEBUG=False DJANGO_SECRET_KEY=build-only-not-a-runtime-key \
    KITCHEN_DATA_DIR=/tmp/build \
    python manage.py collectstatic --noinput \
 && DJANGO_DEBUG=False DJANGO_SECRET_KEY=build-only-not-a-runtime-key \
    KITCHEN_DATA_DIR=/tmp/build \
    python manage.py compilemessages --ignore=.venv \
 && rm -rf /tmp/build

# The bind mount goes here. Created and owned now so the container comes up even
# on a fresh volume.
RUN mkdir -p /data && chown kitchen:kitchen /data
VOLUME ["/data"]

USER kitchen
EXPOSE 8000

# Plain HTTP on localhost, because that is what the health check is for and what
# the reverse proxy speaks to. config/health.py explains why it says so little.
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

ENTRYPOINT ["/app/deploy/entrypoint.sh"]

# Two workers, two threads — not the usual (2 x cores) + 1. This is a household
# app on a two-core NAS that also runs DSM, Home Assistant and a backup, and
# each worker is a whole Python interpreter. Two is enough that one slow
# photograph upload does not block the person reading a recipe off a phone.
#
# Comments cannot go inside the JSON array below: exec-form CMD is parsed as
# JSON, and a `#` line spliced into a line-continued instruction is not
# reliably stripped.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--threads", "2", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
