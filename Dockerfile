# pull official base image
FROM ubuntu:20.04

# https://rtfm.co.ua/en/docker-configure-tzdata-and-timezone-during-build/
ENV TZ=Europe/London
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

RUN apt update
RUN apt install -y swig g++ python3-dev libpqxx-dev rapidjson-dev libexpat1-dev libboost-filesystem-dev
RUN apt install -y libpqxx-dev libboost-program-options-dev libprotobuf-dev zlib1g-dev libboost-iostreams-dev
RUN apt install -y python3-pip protobuf-compiler

# set work directory
WORKDIR /usr/src/app

RUN pip3 install --upgrade pip
COPY ./requirements.txt .
RUN pip3 install -r requirements.txt

COPY ./pgmap pgmap

WORKDIR /usr/src/app/pgmap/cppo5m

RUN protoc -I=proto proto/osmformat.proto --cpp_out=pbf
RUN protoc -I=proto proto/fileformat.proto --cpp_out=pbf

WORKDIR /usr/src/app/pgmap

RUN python3 setup.py build
RUN python3 setup.py install

WORKDIR /usr/src/app

# set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# copy project
COPY . .


