# Based on https://testdriven.io/blog/dockerizing-django-with-postgres-gunicorn-and-nginx/
# pull official base image
# Pin the Debian release: pgmap needs libpqxx 7.x, and later releases ship libpqxx 8.
FROM python:3.14-slim-trixie

# https://rtfm.co.ua/en/docker-configure-tzdata-and-timezone-during-build/
ENV TZ=Europe/London

# Python headers come from the base image, so python3-dev is not installed
# (it would pull in Debian's separate system Python).
RUN apt-get update && apt-get install -y --no-install-recommends \
      tzdata swig g++ libpqxx-dev rapidjson-dev libexpat1-dev zlib1g-dev \
      libboost-filesystem-dev libboost-program-options-dev libboost-iostreams-dev \
      libprotobuf-dev protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# set work directory
WORKDIR /usr/src/app

RUN python3 -m venv /opt/env
ENV PATH="/opt/env/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Cache pip downloads between builds, so changing requirements.txt only
# downloads the packages that changed. The cache is not stored in the image.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install pip==26.2.1
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install setuptools==84.0.0 wheel==0.48.0 packaging
COPY ./requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install -r requirements.txt

# Generate protobuf sources in a separate cached layer. Changes to pgmap C++
# code do not require regenerating these files.
RUN --mount=type=bind,source=pgmap/cppo5m/proto,target=/tmp/pgmap-proto \
    mkdir -p /tmp/pgmap-pbf && \
    protoc -I=/tmp/pgmap-proto \
      /tmp/pgmap-proto/osmformat.proto /tmp/pgmap-proto/fileformat.proto \
      --cpp_out=/tmp/pgmap-pbf

# Build pgmap from a temporary bind mount of its source, so the source is not
# kept in the image. The mount is writable for the build, but changes are
# discarded. Only changes to pgmap trigger a recompile.
# Compiler flags for pgmap, e.g. --build-arg PGMAP_CFLAGS="-g -O0" to debug.
ARG PGMAP_CFLAGS="-g0 -O1"
RUN --mount=type=bind,source=pgmap,target=/tmp/pgmap-src,rw \
    cd /tmp/pgmap-src && \
    cp /tmp/pgmap-pbf/* cppo5m/pbf/ && \
    pip install --no-build-isolation --no-deps .

# Verify the installed bindings independently of the build source directory.
WORKDIR /tmp
RUN python3 -c "import pgmap; assert hasattr(pgmap, 'mapstringstring')"

# The application code is not copied into the image: docker-compose.yml mounts
# the source directory at /usr/src/app instead.
WORKDIR /usr/src/app
