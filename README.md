# MICROs to DOCKER

## Alcance

_Migración de una arquitectura de microservicios Java a Docker. El objetivo es reemplazar las máquinas virtuales actuales por contenedores._

## Cómo levantar el ambiente
Parado en la carpeta **appTest**, ejecutar `docker compose up -d`. Para bajarlo, `docker compose down`.

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

# LDAP

Servidor de directorio centralizado de usuarios basado en OpenLDAP. Permite autenticación y autorización centralizada para todos los servicios de la plataforma.

## Imagen

```
osixia/openldap:1.5.0
```

## Motivo de la imagen

No existe imagen oficial en Docker Hub para OpenLDAP. La imagen de Bitnami fue discontinuada. Se utilizó `osixia/openldap` por ser la más mantenida y documentada de la comunidad.

## Variables de entorno

| Variable | Descripción |
|---|---|
| `LDAP_ROOT` | Sufijo base del directorio. Ej: `dc=empresa,dc=com` |
| `LDAP_ADMIN_USERNAME` | Usuario administrador del directorio |
| `LDAP_ADMIN_PASSWORD` | Contraseña del administrador |
| `LDAP_ORGANISATION` | Nombre de la organización raíz del árbol |

## Volúmenes

| Nombre | Ruta en contenedor | Descripción |
|---|---|---|
| `ldap_data` | `/var/lib/ldap` | Datos del directorio LDAP |
| `ldap_config` | `/etc/ldap/slapd.d` | Configuración del servidor slapd |

## Puertos

No se exponen puertos al host. El servicio es accesible únicamente dentro de la red interna Docker por nombre de contenedor (`ldap`) en los puertos `389` (LDAP) y `636` (LDAPS).

## Conceptos clave aprendidos

- **LDAP (Lightweight Directory Access Protocol):** protocolo para acceder a directorios de usuarios organizados en forma de árbol jerárquico.
- **Sufijo base (`dc=empresa,dc=com`):** raíz del árbol LDAP. Cada `dc=` representa un nivel del dominio.
- **Dos volúmenes separados:** los datos del directorio y la configuración del servidor se persisten de forma independiente para facilitar backups y migraciones.
- **Sin exposición de puertos al host:** LDAP no necesita ser accesible desde fuera de Docker; los servicios lo consumen internamente por nombre de contenedor.

---

## Dependencias de arranque (depends_on)

Al agregar LDAP al proyecto, el orden de arranque de los contenedores cambió
y comenzaron a aparecer errores de resolución de nombres en los HAProxy:

- `haproxy1-int` no podía resolver `java1` y `java2`
- `haproxy1-ssl` no podía resolver `haproxy1-int`

La causa es que HAProxy intenta resolver los nombres de los servidores backend
al momento de leer la configuración. Si el contenedor destino no está listo,
falla con `could not resolve address`.

### Solución

Se agregaron dependencias explícitas con `depends_on` en el docker-compose:

- `haproxy1-int` depende de `java1` y `java2` con `condition: service_started`
- `haproxy1-ssl` depende de `haproxy1-int` con `condition: service_healthy`

Para que `service_healthy` funcione, se agregó un healthcheck a `haproxy1-int`
que valida la configuración con el propio binario de HAProxy:
```yaml
healthcheck:
  test: ["CMD", "haproxy", "-c", "-f", "/usr/local/etc/haproxy/haproxy.cfg"]
  interval: 5s
  timeout: 3s
  retries: 5
```

La imagen `haproxy:2.8` no incluye `wget` ni `curl`, por lo que no es posible
hacer un healthcheck HTTP. Se usa el binario nativo como alternativa.

### Stats de HAProxy

Se habilitó el frontend de estadísticas en `haproxy1-int` agregando al cfg:
```
frontend stats
    bind *:8404
    stats enable
    stats uri /stats
```

Mapeando el puerto `8404:8404` en el docker-compose es accesible desde el host
en `http://localhost:8404/stats`.

## Conceptos clave aprendidos

- El orden de arranque en Docker Compose sin `depends_on` es no determinista.
- `service_started` alcanza cuando solo necesitás que el proceso haya iniciado.
- `service_healthy` requiere que el contenedor tenga un healthcheck definido y lo pase.
- Renombrar la carpeta raíz cambia el nombre de la red y el prefijo de los contenedores —
  los contenedores anteriores quedan huérfanos y hay que eliminarlos manualmente.

# Registro de puertos

Relevamiento de puertos en uso por todos los contenedores del proyecto. Los contenedores sin puerto de host son accesibles únicamente dentro de la red interna Docker.

| Contenedor | Puerto host | Puerto contenedor | Descripción |
|---|---|---|---|
| `haproxy1-ssl` | `8080` | `443` | HTTPS externo (SSL termination) |
| `haproxy1-int` | `443` | `80` | Balanceo interno round-robin |
| `java1` | - | - | Solo red interna Docker |
| `java2` | - | - | Solo red interna Docker |
| `redis-master` | - | `6379` | Solo red interna Docker |
| `redis-slave` | - | `6379` | Solo red interna Docker |
| `redis-sentinel` | - | `26379` | Solo red interna Docker |
| `solr` | `8983` | `8983` | Panel admin Solr |
| `webdav` | `8081` | `80` | WebDAV HTTP |
| `ldap` | - | `389 / 636` | Solo red interna Docker |


![Arquitectura Docker appTest](docs/arq.svg)
