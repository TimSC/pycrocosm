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
RUN pip3 install pip==24.2
RUN pip3 install setuptools==75.1.0 wheel==0.44.0
COPY ./requirements.txt .
RUN pip3 install -r requirements.txt

# copy project
COPY . .
#COPY ./pgmap pgmap

WORKDIR /usr/src/app/pgmap/cppo5m

RUN protoc -I=proto proto/osmformat.proto --cpp_out=pbf
RUN protoc -I=proto proto/fileformat.proto --cpp_out=pbf

WORKDIR /usr/src/app/pgmap

RUN python3 setup.py build
RUN python3 setup.py install

WORKDIR /usr/src/app

# set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1


