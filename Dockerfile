# Based on https://testdriven.io/blog/dockerizing-django-with-postgres-gunicorn-and-nginx/
# pull official base image
FROM ubuntu:24.04

# https://rtfm.co.ua/en/docker-configure-tzdata-and-timezone-during-build/
ENV TZ=Europe/London
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

RUN apt update
RUN apt install -y swig g++ python3-dev libpqxx-dev rapidjson-dev libexpat1-dev libboost-filesystem-dev
RUN apt install -y libpqxx-dev libboost-program-options-dev libprotobuf-dev zlib1g-dev libboost-iostreams-dev
RUN apt install -y python3-pip protobuf-compiler python3-venv

# set work directory
WORKDIR /usr/src/app

RUN python3 -m venv /opt/env
ENV PATH="/opt/env/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
RUN pip3 install pip==24.2
RUN pip3 install setuptools==75.1.0 wheel==0.44.0 packaging
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
RUN cp /tmp/pgmap-pbf/* cppo5m/pbf/ && \
    pip install --no-build-isolation --no-deps .

# Verify the installed bindings independently of the build source directory.
WORKDIR /tmp
RUN python3 -c "import pgmap; assert hasattr(pgmap, 'mapstringstring')"

WORKDIR /usr/src/app
