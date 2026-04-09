"""
traffic-generator — appTest
Genera tráfico sostenido y variado hacia todos los componentes de la arquitectura.
No es un test de estrés: el objetivo es que los dashboards de Grafana tengan datos
con forma real (picos, valles, operaciones mixtas, conexiones simultáneas).
"""

import threading
import time
import random
import logging
import os
import string
import json
from datetime import datetime

import redis
import requests
from requests.auth import HTTPDigestAuth

# ─────────────────────────────────────────────
# CONFIGURACIÓN CENTRAL — ajustar acá sin tocar
# el resto del código
# ─────────────────────────────────────────────

CFG = {
    # HAProxy SSL (entrada principal)
    "haproxy_ssl_host": "haproxy1-ssl",
    "haproxy_ssl_port": 443,

    # HAProxy INT (acceso directo interno)
    "haproxy_int_host": "haproxy1-int",
    "haproxy_int_port": 80,

    # Redis
    "redis_host": "redis-master",
    "redis_port": 6379,
    "redis_password": "muyfacil",

    # Solr
    "solr_host": "solr",
    "solr_port": 8983,
    "solr_core": "test",

    # WebDAV
    "webdav_host": "webdav",
    "webdav_port": 80,
    "webdav_user": "admin",
    "webdav_pass": "admin",

    # Intensidad por componente (requests o ops por ciclo)
    "haproxy_workers":  5,   # threads golpeando HAProxy
    "redis_workers":    4,   # threads operando Redis
    "solr_workers":     3,   # threads consultando Solr
    "webdav_workers":   2,   # threads operando WebDAV

    # Delay base entre operaciones (segundos) — con variación aleatoria
    "haproxy_delay":  0.3,
    "redis_delay":    0.1,
    "solr_delay":     0.5,
    "webdav_delay":   1.0,

    # Cada cuántos segundos se loguea un resumen de estadísticas
    "stats_interval": 15,
}

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("traffic-gen")

# ─────────────────────────────────────────────
# CONTADORES GLOBALES (thread-safe con lock)
# ─────────────────────────────────────────────

stats = {
    "haproxy_ok": 0, "haproxy_err": 0,
    "redis_ok":   0, "redis_err":   0,
    "solr_ok":    0, "solr_err":    0,
    "webdav_ok":  0, "webdav_err":  0,
}
stats_lock = threading.Lock()

def inc(key):
    with stats_lock:
        stats[key] += 1

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def jitter(base):
    """Retorna base ± 40% aleatoriamente para evitar tráfico perfectamente uniforme."""
    return base * random.uniform(0.6, 1.4)

def rand_string(length=8):
    return ''.join(random.choices(string.ascii_lowercase, k=length))

def rand_int(a=1, b=1000):
    return random.randint(a, b)

# ─────────────────────────────────────────────
# WORKER: HAProxy
# Simula usuarios navegando: GET a distintas rutas,
# algunos con headers de usuario, algunos con query params.
# Va directo a haproxy1-int por HTTP interno para
# evitar problemas de certificado SSL autofirmado.
# ─────────────────────────────────────────────

HAPROXY_ROUTES = [
    "/",
    "/index.html",
    "/health",
    "/api/users",
    "/api/products",
    "/api/search?q=test",
    "/api/orders",
    "/static/app.js",
    "/static/style.css",
    "/favicon.ico",
]

HAPROXY_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) Firefox/121.0",
    "curl/8.4.0",
    "python-requests/2.31.0",
]

def worker_haproxy(worker_id):
    logger = logging.getLogger(f"haproxy.w{worker_id}")
    base_url = f"http://{CFG['haproxy_int_host']}:{CFG['haproxy_int_port']}"
    session = requests.Session()

    while True:
        route = random.choice(HAPROXY_ROUTES)
        ua    = random.choice(HAPROXY_USER_AGENTS)
        headers = {
            "User-Agent": ua,
            "X-Request-ID": rand_string(16),
            "Accept": "text/html,application/json,*/*",
        }

        # 80% GET, 15% POST, 5% HEAD
        roll = random.random()
        try:
            if roll < 0.80:
                r = session.get(f"{base_url}{route}", headers=headers, timeout=3)
            elif roll < 0.95:
                payload = {"user": rand_string(), "value": rand_int()}
                r = session.post(f"{base_url}{route}", json=payload, headers=headers, timeout=3)
            else:
                r = session.head(f"{base_url}{route}", headers=headers, timeout=3)

            # HAProxy devuelve 200, 404, 502, etc. — todos son tráfico válido
            inc("haproxy_ok")
            logger.debug(f"{r.request.method} {route} → {r.status_code}")

        except Exception as e:
            inc("haproxy_err")
            logger.warning(f"Error en {route}: {e}")

        time.sleep(jitter(CFG["haproxy_delay"]))

# ─────────────────────────────────────────────
# WORKER: Redis
# Mezcla de operaciones para que el dashboard
# muestre comandos/s, hit ratio, memoria, keyspace.
# ─────────────────────────────────────────────

def worker_redis(worker_id):
    logger = logging.getLogger(f"redis.w{worker_id}")

    r = redis.Redis(
        host=CFG["redis_host"],
        port=CFG["redis_port"],
        password=CFG["redis_password"],
        decode_responses=True,
        socket_connect_timeout=5,
    )

    # Verificar conexión al arrancar
    try:
        r.ping()
        logger.info("Conectado a Redis")
    except Exception as e:
        logger.error(f"No se pudo conectar a Redis: {e}")
        return

    while True:
        try:
            op = random.choices(
                ["set", "get", "incr", "list", "hash", "expire", "del"],
                weights=[25, 30, 15, 10, 10, 5, 5],
            )[0]

            if op == "set":
                key = f"tg:key:{rand_string(6)}"
                val = rand_string(random.randint(10, 200))
                ttl = random.randint(30, 300)
                r.setex(key, ttl, val)

            elif op == "get":
                # Mezcla de keys que existen y algunas que no (genera misses)
                if random.random() < 0.6:
                    key = f"tg:key:{rand_string(6)}"   # probable miss
                else:
                    # Intentar recuperar una key reciente (probable hit)
                    keys = r.keys("tg:key:*")
                    key = random.choice(keys) if keys else f"tg:key:{rand_string(6)}"
                r.get(key)

            elif op == "incr":
                counter = f"tg:counter:{random.randint(1, 20)}"
                r.incr(counter)
                r.expire(counter, 600)

            elif op == "list":
                listkey = f"tg:list:{random.randint(1, 5)}"
                r.lpush(listkey, rand_string())
                r.ltrim(listkey, 0, 99)   # cap en 100 elementos
                r.lrange(listkey, 0, 9)

            elif op == "hash":
                hkey = f"tg:hash:{random.randint(1, 10)}"
                field = rand_string(4)
                r.hset(hkey, field, rand_int())
                r.hgetall(hkey)
                r.expire(hkey, 600)

            elif op == "expire":
                keys = r.keys("tg:*")
                if keys:
                    r.expire(random.choice(keys), random.randint(60, 300))

            elif op == "del":
                keys = r.keys("tg:key:*")
                if keys:
                    r.delete(random.choice(keys))

            inc("redis_ok")

        except Exception as e:
            inc("redis_err")
            logger.warning(f"Error en operación Redis: {e}")
            time.sleep(2)  # backoff en error

        time.sleep(jitter(CFG["redis_delay"]))

# ─────────────────────────────────────────────
# WORKER: Solr
# Queries al core "test" y escritura de documentos.
# Muestra query rate, latencia, documentos indexados.
# ─────────────────────────────────────────────

SOLR_SEARCH_TERMS = [
    "test", "docker", "nginx", "redis", "haproxy",
    "solr", "java", "linux", "python", "network",
    "alpha", "beta", "gamma", "delta", "sigma",
]

def worker_solr(worker_id):
    logger = logging.getLogger(f"solr.w{worker_id}")
    base = f"http://{CFG['solr_host']}:{CFG['solr_port']}/solr/{CFG['solr_core']}"
    session = requests.Session()

    while True:
        try:
            op = random.choices(
                ["query", "query_facet", "add_doc", "delete_doc"],
                weights=[50, 20, 25, 5],
            )[0]

            if op == "query":
                term = random.choice(SOLR_SEARCH_TERMS)
                rows = random.choice([5, 10, 20, 50])
                params = {
                    "q": f"content:{term}",
                    "rows": rows,
                    "wt": "json",
                    "fl": "id,title,content",
                }
                r = session.get(f"{base}/select", params=params, timeout=5)
                inc("solr_ok") if r.ok else inc("solr_err")

            elif op == "query_facet":
                params = {
                    "q": "*:*",
                    "facet": "true",
                    "facet.field": "category",
                    "rows": 0,
                    "wt": "json",
                }
                r = session.get(f"{base}/select", params=params, timeout=5)
                inc("solr_ok") if r.ok else inc("solr_err")

            elif op == "add_doc":
                doc = {
                    "id": f"tg-{rand_string(10)}",
                    "title": f"Traffic gen doc {rand_string(6)}",
                    "content": " ".join(random.choices(SOLR_SEARCH_TERMS, k=random.randint(3, 10))),
                    "category": random.choice(["web", "api", "backend", "cache", "search"]),
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "score_i": rand_int(1, 100),
                }
                r = session.post(
                    f"{base}/update?commit=true",
                    json=[doc],
                    headers={"Content-Type": "application/json"},
                    timeout=5,
                )
                inc("solr_ok") if r.ok else inc("solr_err")

            elif op == "delete_doc":
                # Borrar documentos viejos generados por este worker
                r = session.post(
                    f"{base}/update?commit=true",
                    json={"delete": {"query": f"id:tg-* AND score_i:[1 TO {rand_int(1,30)}]"}},
                    headers={"Content-Type": "application/json"},
                    timeout=5,
                )
                inc("solr_ok") if r.ok else inc("solr_err")

        except Exception as e:
            inc("solr_err")
            logger.warning(f"Error Solr: {e}")
            time.sleep(3)

        time.sleep(jitter(CFG["solr_delay"]))

# ─────────────────────────────────────────────
# WORKER: WebDAV
# PUT de archivos pequeños, GET, DELETE.
# ─────────────────────────────────────────────

def worker_webdav(worker_id):
    logger = logging.getLogger(f"webdav.w{worker_id}")
    base = f"http://{CFG['webdav_host']}:{CFG['webdav_port']}/uploads"
    auth = HTTPDigestAuth(CFG["webdav_user"], CFG["webdav_pass"])
    session = requests.Session()
    uploaded_files = []

    while True:
        try:
            op = random.choices(
                ["put", "get", "delete", "propfind"],
                weights=[40, 35, 15, 10],
            )[0]

            if op == "put":
                filename = f"tg-{rand_string(8)}.txt"
                content = f"Traffic generator file\n{rand_string(100)}\n{datetime.utcnow().isoformat()}"
                r = session.put(
                    f"{base}/{filename}",
                    data=content.encode(),
                    auth=auth,
                    headers={"Content-Type": "text/plain"},
                    timeout=5,
                )
                if r.ok:
                    uploaded_files.append(filename)
                    # Mantener lista acotada
                    if len(uploaded_files) > 50:
                        uploaded_files.pop(0)
                inc("webdav_ok") if r.ok else inc("webdav_err")

            elif op == "get" and uploaded_files:
                filename = random.choice(uploaded_files)
                r = session.get(f"{base}/{filename}", auth=auth, timeout=5)
                inc("webdav_ok") if r.ok else inc("webdav_err")

            elif op == "delete" and uploaded_files:
                filename = uploaded_files.pop(random.randrange(len(uploaded_files)))
                r = session.request("DELETE", f"{base}/{filename}", auth=auth, timeout=5)
                inc("webdav_ok") if r.ok else inc("webdav_err")

            elif op == "propfind":
                r = session.request(
                    "PROPFIND", f"{base}/",
                    auth=auth,
                    headers={"Depth": "1"},
                    timeout=5,
                )
                inc("webdav_ok") if r.ok else inc("webdav_err")

        except Exception as e:
            inc("webdav_err")
            logger.warning(f"Error WebDAV: {e}")
            time.sleep(3)

        time.sleep(jitter(CFG["webdav_delay"]))

# ─────────────────────────────────────────────
# STATS REPORTER
# Cada N segundos imprime un resumen de operaciones.
# ─────────────────────────────────────────────

def stats_reporter():
    logger = logging.getLogger("stats")
    while True:
        time.sleep(CFG["stats_interval"])
        with stats_lock:
            snap = dict(stats)

        total_ok  = sum(v for k, v in snap.items() if k.endswith("_ok"))
        total_err = sum(v for k, v in snap.items() if k.endswith("_err"))

        logger.info(
            f"── Resumen ──────────────────────────────────────────\n"
            f"  HAProxy : {snap['haproxy_ok']:>6} ok  {snap['haproxy_err']:>4} err\n"
            f"  Redis   : {snap['redis_ok']:>6} ok  {snap['redis_err']:>4} err\n"
            f"  Solr    : {snap['solr_ok']:>6} ok  {snap['solr_err']:>4} err\n"
            f"  WebDAV  : {snap['webdav_ok']:>6} ok  {snap['webdav_err']:>4} err\n"
            f"  TOTAL   : {total_ok:>6} ok  {total_err:>4} err\n"
            f"────────────────────────────────────────────────────"
        )

# ─────────────────────────────────────────────
# ARRANQUE
# ─────────────────────────────────────────────

def wait_for_services():
    """Espera pasiva al inicio para que los demás contenedores levanten."""
    wait = int(os.environ.get("STARTUP_WAIT", "15"))
    log.info(f"Esperando {wait}s para que los servicios estén listos...")
    time.sleep(wait)

def main():
    log.info("=" * 55)
    log.info("  traffic-generator — appTest")
    log.info("=" * 55)

    wait_for_services()

    threads = []

    # HAProxy workers
    for i in range(CFG["haproxy_workers"]):
        t = threading.Thread(target=worker_haproxy, args=(i,), daemon=True, name=f"haproxy-{i}")
        threads.append(t)

    # Redis workers
    for i in range(CFG["redis_workers"]):
        t = threading.Thread(target=worker_redis, args=(i,), daemon=True, name=f"redis-{i}")
        threads.append(t)

    # Solr workers
    for i in range(CFG["solr_workers"]):
        t = threading.Thread(target=worker_solr, args=(i,), daemon=True, name=f"solr-{i}")
        threads.append(t)

    # WebDAV workers
    for i in range(CFG["webdav_workers"]):
        t = threading.Thread(target=worker_webdav, args=(i,), daemon=True, name=f"webdav-{i}")
        threads.append(t)

    # Stats reporter
    t = threading.Thread(target=stats_reporter, daemon=True, name="stats")
    threads.append(t)

    log.info(f"Lanzando {len(threads)} threads...")
    for t in threads:
        t.start()
        log.info(f"  ✓ {t.name}")

    log.info("Generador activo. Ctrl+C para detener.")

    # Mantener el proceso principal vivo
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Deteniendo generador...")

if __name__ == "__main__":
    main()
