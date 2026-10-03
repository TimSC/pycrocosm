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
RUN pip3 install pip==26.2.1
RUN pip3 install setuptools==84.0.0 wheel==0.48.0 packaging
COPY ./requirements.txt .
RUN pip3 install -r requirements.txt

# Generate protobuf sources in a separate cached layer. Changes to pgmap C++
# code do not require regenerating these files.
COPY ./pgmap/cppo5m/proto/ /tmp/pgmap-proto/
RUN mkdir -p /tmp/pgmap-pbf && \
    protoc -I=/tmp/pgmap-proto \
      /tmp/pgmap-proto/osmformat.proto /tmp/pgmap-proto/fileformat.proto \
      --cpp_out=/tmp/pgmap-pbf

# Copy frequently changing code only after all external dependencies.
COPY . .

WORKDIR /usr/src/app/pgmap
# Compiler flags for pgmap, e.g. --build-arg PGMAP_CFLAGS="-g -O0" to debug.
ARG PGMAP_CFLAGS="-g0 -O1"
RUN cp /tmp/pgmap-pbf/* cppo5m/pbf/ && \
    pip install --no-build-isolation --no-deps .

# Verify the installed bindings independently of the build source directory.
WORKDIR /tmp
RUN python3 -c "import pgmap; assert hasattr(pgmap, 'mapstringstring')"

WORKDIR /usr/src/app
