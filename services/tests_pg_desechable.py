"""Postgres DESECHABLE para las pruebas que necesitan un Postgres de verdad
(indice unico parcial, UPDATE ... RETURNING, carreras entre conexiones).

NO es dependencia de produccion y no corre por defecto: las pruebas que lo usan
se saltan solas si no hay con que levantarlo. Requisitos:

  PG_BIN                  carpeta bin de un PostgreSQL 16 (initdb, pg_ctl, postgres), p. ej. el zip
                          de binarios de EnterpriseDB para Windows o una instalacion local.
  MIGRACION_0002_SQL      ruta a 0002_mensajes_estado.sql (migracion de la fase C; vive en
                          techo360-chatbot/migrations/ cuando se integre).

El servidor escucha solo en 127.0.0.1, con autenticacion trust, en un directorio
temporal que se borra al terminar. No toca ninguna base real.
"""
import atexit
import os
import pathlib
import shutil
import subprocess
import tempfile

DDL_0001 = """
CREATE TABLE IF NOT EXISTS conversaciones (
    id BIGSERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL,
    consentimiento_entrenamiento BOOLEAN NOT NULL,
    fecha TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conversaciones_usuario_id ON conversaciones (usuario_id);
CREATE TABLE IF NOT EXISTS mensajes (
    id BIGSERIAL PRIMARY KEY,
    conversacion_id BIGINT NOT NULL REFERENCES conversaciones (id) ON DELETE CASCADE,
    rol TEXT NOT NULL CHECK (rol IN ('usuario', 'asistente')),
    contenido TEXT NOT NULL,
    feedback_util BOOLEAN,
    fecha TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mensajes_conversacion_id ON mensajes (conversacion_id);
"""


def migracion_0002() -> str | None:
    candidatas = [os.environ.get("MIGRACION_0002_SQL")]
    aqui = pathlib.Path(__file__).resolve().parent.parent
    candidatas.append(str(aqui.parent / "techo360-chatbot" / "migrations" / "0002_mensajes_estado.sql"))
    for ruta in candidatas:
        if ruta and pathlib.Path(ruta).is_file():
            return pathlib.Path(ruta).read_text(encoding="utf-8")
    return None


def disponible() -> bool:
    return bool(os.environ.get("PG_BIN")) and migracion_0002() is not None


class ServidorDesechable:
    def __init__(self, pg_bin: str, puerto: int = 55434):
        self.bin = pathlib.Path(pg_bin)
        self.dir = tempfile.mkdtemp(prefix="pg_desechable_")
        self.puerto = puerto
        exe = ".exe" if os.name == "nt" else ""
        self.pg_ctl = str(self.bin / f"pg_ctl{exe}")
        nada = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([str(self.bin / f"initdb{exe}"), "-D", self.dir, "-U", "postgres", "--auth=trust", "--encoding=utf8"], check=True, **nada)
        subprocess.run(
            [self.pg_ctl, "-D", self.dir, "-o", f"-p {puerto} -c listen_addresses=127.0.0.1 -c fsync=off", "-w",
             "-l", os.path.join(self.dir, "log.txt"), "start"],
            check=True, **nada,
        )
        atexit.register(self.parar)

    def url(self, base: str = "postgres") -> str:
        return f"postgresql+psycopg2://postgres@127.0.0.1:{self.puerto}/{base}"

    def parar(self):
        subprocess.run([self.pg_ctl, "-D", self.dir, "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(self.dir, ignore_errors=True)


def crear_base_con_esquema(servidor: ServidorDesechable, nombre: str):
    """Base nueva con el esquema REAL de postgres-vector (0001 + 0002). Devuelve su URL."""
    import psycopg2

    admin = psycopg2.connect(servidor.url().replace("+psycopg2", ""))
    admin.autocommit = True
    admin.cursor().execute(f'CREATE DATABASE "{nombre}"')
    admin.close()
    base = servidor.url(nombre)
    con = psycopg2.connect(base.replace("+psycopg2", ""))
    con.autocommit = True
    cur = con.cursor()
    cur.execute(DDL_0001)
    # 0002 exige que existan los 6 mensajes del backfill (ids 26, 45, 48, 55, 56, 57):
    # se crean como en produccion y la propia migracion los pasa a 'error'... y luego
    # se borran para partir de una base limpia.
    for i, mid in enumerate((26, 45, 48, 55, 56, 57)):  # uno por conversacion, como en produccion
        cur.execute("INSERT INTO conversaciones (id, usuario_id, consentimiento_entrenamiento) VALUES (%s, 0, false)", (900 + i,))
        cur.execute("INSERT INTO mensajes (id, conversacion_id, rol, contenido) VALUES (%s, %s, 'usuario', 'x')", (mid, 900 + i))
    cur.execute(migracion_0002())
    cur.execute("DELETE FROM conversaciones WHERE id BETWEEN 900 AND 905")  # cascada: borra esos 6
    cur.execute("SELECT setval('mensajes_id_seq', 1000); SELECT setval('conversaciones_id_seq', 1000)")
    con.close()
    return base
