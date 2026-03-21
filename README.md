# GDE2

## Alcance

_Migración de una arquitectura de microservicios Java a Docker. El objetivo es reemplazar las máquinas virtuales actuales por contenedores._

## Cómo levantar el ambiente
Parado en la carpeta **gde2**, ejecutar `docker compose up -d`. Para bajarlo, `docker compose down`.

## Estructura de archivos
Cada componente tiene su propia carpeta con un **Dockerfile** y su archivo de configuración. El **docker-compose.yml** está en la raíz y orquesta todos los servicios.

## Volúmenes
Los archivos de configuración se montan como bind mounts. Los datos persistentes se almacenan en named volumes administrados por Docker.


## HAProxy
Se crearon dos instancias de HAProxy basadas en la imagen **haproxy:2.8.** Cada una tiene su propio Dockerfile y archivo haproxy.cfg montado via bind mount.

**_haproxy1-ssl_**: recibe el tráfico externo en el puerto **443** y lo reenvía al HAProxy interno.

**_haproxy1-int_**: recibe el tráfico del SSL por el puerto **80** y lo balancea entre los servicios Java usando round robin.

**_java1 y java2_**: contenedores nginx que simulan los servicios Java reales, accesibles solo dentro de la red interna de Docker.

## Redis cluster
Se configuró un cluster de Redis compuesto por tres servicios:

**redis-master**: imagen **redis:8.2.5-alpine**, configurado con autenticación, persistencia RDB en named volume **redis-master** y bind en **0.0.0.0:6379**.

**redis-slave**: imagen **redis:8.2.5-alpine**, replica al master via directiva replicaof **redis-master 6379**, persistencia RDB en named volume **redis-slave**.

**redis-sentinel**: imagen **bitnami/redis-sentinel:latest**. Se descartó usar **redis:8.2.5-alpine** con **sentinel.conf** debido a un bug en Redis 8 donde el hostname del master se resuelve durante la lectura del archivo de configuración, antes de que la red de Docker esté disponible. La imagen de Bitnami resuelve esto configurando el sentinel via variables de entorno. El sentinel monitorea al master bajo el alias mymaster y tiene configurado depends_on con condition: service_healthy para garantizar que master y slave estén listos antes de arrancar.

## Solr
Se agregó el servicio solr basado en la imagen **solr:9.10.1**. Se eligió esta versión por sobre la 10.0.0 siguiendo el criterio de no migrar y actualizar simultáneamente.
El servicio expone el puerto **8983** hacia la máquina host, donde está disponible la interfaz de administración web. 
Los datos se persisten en un named volume montado en **/var/solr** dentro del contenedor.

Se creó un core de prueba llamado **test** usando el comando:

`bashdocker exec -it solr solr create_core -c test`

Se verificó que el core persiste correctamente ante reinicios del contenedor mediante `docker compose stop solr` y `docker compose start solr`.

## WebDAV

Servidor de archivos compartidos sobre HTTP usando Apache HTTP Server con `mod_dav`.

**Imagen base:** `httpd:2.4-alpine`

**Puerto:** `8081→80`

**Volúmenes:**
- `webdav` → `/usr/local/apache2/htdocs/`
- `webdav-up` → `/usr/local/apache2/uploads/` (archivos subidos por WebDAV)

**Estructura de archivos:**
```
webdav/
    Dockerfile
    httpd.conf
    httpd-dav.conf
    user.passwd
```

**Módulos Apache habilitados en `httpd.conf`:**
```
LoadModule dav_module modules/mod_dav.so
LoadModule dav_fs_module modules/mod_dav_fs.so
LoadModule dav_lock_module modules/mod_dav_lock.so
LoadModule auth_digest_module modules/mod_auth_digest.so
LoadModule socache_dbm_module modules/mod_socache_dbm.so
```

**Notas importantes:**
- Se usa `httpd-dav.conf` para configurar el endpoint `/uploads` con autenticación Digest
- Se requiere instalar `apr-util-dbm_gdbm` vía `apk` porque la imagen Alpine no incluye el driver DBM necesario para `DavLockDB`
- El archivo `user.passwd` se genera con `htdigest` dentro del contenedor y se copia al proyecto

**Cómo regenerar `user.passwd`:**
```bash
docker exec -it webdav htdigest -c /usr/local/apache2/user.passwd DAV-upload admin
docker cp webdav:/usr/local/apache2/user.passwd ./webdav/
```

**Verificar que WebDAV funciona:**
```bash
curl -v -X OPTIONS http://localhost:8081/uploads/
# Debe mostrar: PROPFIND, PROPPATCH, COPY, MOVE, LOCK, UNLOCK, DELETE

curl -v -T archivo.txt --digest -u admin:password http://localhost:8081/uploads/
# Debe responder: 201 Created
```





# Estado actual del **docker ps**

 
* haproxy1-ssl
* haproxy1-int
* java1
* java2
* redis-master
* redis-slave
* redis-sentinel
* solr

![Arquitectura Docker GDE2](docs/arq.png)
