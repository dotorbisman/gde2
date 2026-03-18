# GDE2

## Alcance

_Migración de una arquitectura de microservicios Java a Docker. El objetivo es reemplazar las máquinas virtuales actuales por contenedores._

## Estado actual

Se levantaron cuatro contenedores usando Docker Compose:

**_haproxy1-ssl_**: recibe el tráfico externo en el puerto **8080** y lo reenvía al HAProxy interno.

**_haproxy1-int_**: recibe el tráfico del SSL y lo balancea entre los servicios Java usando round robin.

**_java1 y java2_**: contenedores nginx que simulan los servicios Java reales, accesibles solo dentro de la red interna de Docker.

## Cómo levantar el ambiente
Parado en la carpeta **gde2**, ejecutar `docker compose up -d` para inciar. Para bajarlo, `docker compose down`.

## Estructura de archivos
Cada componente tiene su propia carpeta con un **Dockerfile** y su archivo de configuración. El **docker-compose.yml** está en la raíz y orquesta todos los servicios.
