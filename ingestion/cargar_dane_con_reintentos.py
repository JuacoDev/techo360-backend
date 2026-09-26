"""Watchdog de una sola corrida (24-sep-2026) para la carga masiva de
ingestion/actualizar_densidad_dane.py contra 2025-2042, ante un
incidente real y recurrente de red: el relay de Tailscale hacia el VPS
de produccion se cuelga a mitad de una escritura de forma intermitente
(observado en vivo 3 veces la misma tarde -- ver notas de INCIDENTES
abajo, y el AVISO en ingestion/actualizar_densidad_dane.py sobre por
que ni connect_timeout ni statement_timeout ni TCP keepalive bastan
para detectarlo: el patron es un socket que queda "vivo" a nivel de
SO pero deja de transportar datos en ambas direcciones -- un corte que
ninguna de esas protecciones cubre).

NO se mezcla esta logica de reintento dentro de
ingestion/actualizar_densidad_dane.py ni de ingestion/db.py -- es
infraestructura puntual para ESTA carga (2025-2042, un incidente de red
puntual), no un patron que deba heredar el resto del pipeline de
ingestion/.

Por que es seguro reintentar un año completo desde cero: desde el
24-sep-2026, guardar_indicadores_lote() (ver ese modulo) hace COMMIT
POR FILA, no un commit unico al final del lote. Si el proceso muere a
mitad de un año, las filas ya comiteadas quedan persistidas de verdad;
volver a correr el año completo desde el registro 1 simplemente
re-escribe el mismo valor en las filas ya correctas (UPDATE idempotente,
no-op real) y completa las que faltaban. No hace falta trackear en que
fila especifica se corto -- redundante pero simple y robusto, y a
velocidad de red sana un año completo (~1.104 filas) tarda segundos, no
horas.

Mecanismo: lanza ingestion.actualizar_densidad_dane como subproceso, UN
AÑO A LA VEZ (--anio-inicio Y --anio-fin Y), con `python -u` (stdout sin
buffer) para poder medir el progreso real en vivo. Si no aparece ninguna
linea nueva de stdout en LIVENESS_TIMEOUT_S segundos, se asume que el
proceso esta colgado en la misma falla de red ya vista (no un calculo
lento legitimo -- healthy, cada 25 filas comiteadas imprime una linea de
progreso, y a la latencia normal del relay eso son ~5s, muy por debajo
del timeout) y se mata y reintenta el MISMO año desde cero. Si el
subproceso termina con codigo de salida distinto de 0 SIN haberse
colgado (ej. fallo un sanity check), eso es un fallo real de logica o
de datos, no de red -- NO se reintenta, se detiene todo el watchdog y se
reporta.

Limite de MAX_REINTENTOS_POR_ANIO (default 6): si un mismo año se cuelga
mas de eso, se detiene todo el watchdog y se reporta -- podria ya no ser
un corte transitorio sino algo mas serio que necesita intervencion.

Uso:
    python -m ingestion.cargar_dane_con_reintentos --anio-inicio 2025 --anio-fin 2042
    python -m ingestion.cargar_dane_con_reintentos --anio-inicio 2025 --anio-fin 2026 --max-reintentos 3
"""
import argparse
import datetime
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

LIVENESS_TIMEOUT_S = 45
MAX_REINTENTOS_POR_ANIO_DEFAULT = 6

DIR_SCRIPT = Path(__file__).resolve().parent
LOG_INCIDENTES = DIR_SCRIPT / "log_incidentes_carga_dane.jsonl"


def _registrar_incidente(evento: dict) -> None:
    """Log persistente (JSON Lines, append-only) -- evidencia real de un
    incidente de infraestructura, no solo print efimero. Ver AVISO en
    el docstring del modulo: esto documenta cada vez que el watchdog
    mata y reintenta un proceso colgado."""
    evento = {"timestamp": datetime.datetime.now().isoformat(), **evento}
    with LOG_INCIDENTES.open("a", encoding="utf-8") as f:
        f.write(json.dumps(evento, ensure_ascii=False) + "\n")
    print(f"  [INCIDENTE registrado en {LOG_INCIDENTES.name}] {evento}")


def _leer_stdout_en_hilo(proceso, buffer_lineas: list, ultima_actividad: list) -> None:
    """Corre en un hilo separado: lee stdout del subproceso linea por
    linea y actualiza ultima_actividad[0] (timestamp) cada vez que llega
    una -- el hilo principal compara contra esto para detectar
    inactividad sin bloquearse esperando la siguiente linea."""
    for linea in iter(proceso.stdout.readline, ""):
        if not linea:
            break
        buffer_lineas.append(linea.rstrip("\n"))
        ultima_actividad[0] = time.time()
        print(f"    {linea.rstrip(chr(10))}")
    proceso.stdout.close()


def correr_anio_con_reintentos(anio: int, max_reintentos: int, confirmar_sobrescribir: bool) -> tuple[bool, int]:
    """Devuelve (exito, intentos_usados). exito=False solo si se agotaron
    los reintentos por cuelgues repetidos, o si el subproceso fallo por
    una razon que NO es un cuelgue (en ese caso se propaga como
    excepcion, no como False, para distinguir los dos casos)."""
    for intento in range(1, max_reintentos + 1):
        print(f"\n=== Año {anio} -- intento {intento}/{max_reintentos} ===")
        comando = [
            sys.executable, "-u", "-m", "ingestion.actualizar_densidad_dane",
            "--anio-inicio", str(anio), "--anio-fin", str(anio),
        ]
        if confirmar_sobrescribir:
            comando.append("--confirmar-sobrescribir-terridata-proyeccion")

        t0 = time.time()
        proceso = subprocess.Popen(
            comando, cwd=str(DIR_SCRIPT.parent), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        buffer_lineas: list = []
        ultima_actividad = [time.time()]
        hilo = threading.Thread(target=_leer_stdout_en_hilo, args=(proceso, buffer_lineas, ultima_actividad), daemon=True)
        hilo.start()

        colgado = False
        while proceso.poll() is None:
            time.sleep(1)
            if time.time() - ultima_actividad[0] > LIVENESS_TIMEOUT_S:
                colgado = True
                break

        if colgado:
            ultima_linea = buffer_lineas[-1] if buffer_lineas else "(sin ninguna linea de progreso)"
            print(f"\n  SIN ACTIVIDAD por {LIVENESS_TIMEOUT_S}s -- matando el proceso (mismo patron de "
                  f"corte de red ya visto). Ultima linea: {ultima_linea!r}")
            proceso.kill()
            proceso.wait(timeout=10)
            _registrar_incidente({
                "tipo": "cuelgue_detectado_y_reintentado",
                "anio": anio,
                "intento": intento,
                "max_reintentos": max_reintentos,
                "segundos_antes_de_colgarse": round(time.time() - t0, 1),
                "ultima_linea_progreso": ultima_linea,
            })
            if intento == max_reintentos:
                _registrar_incidente({
                    "tipo": "limite_de_reintentos_agotado",
                    "anio": anio,
                    "intentos_totales": intento,
                })
                return False, intento
            continue  # siguiente intento, mismo año

        hilo.join(timeout=5)
        codigo_salida = proceso.returncode
        tiempo_total = round(time.time() - t0, 1)
        if codigo_salida == 0:
            print(f"  Año {anio} completado en intento {intento} ({tiempo_total}s).")
            return True, intento

        # Termino solo (no colgado) pero con error real -- NO es un
        # problema de red, es un fallo de logica/datos/sanity-check.
        # No se reintenta: se detiene todo el watchdog.
        raise RuntimeError(
            f"Año {anio} termino con codigo {codigo_salida} SIN colgarse (no es un incidente de red, "
            f"es un fallo real) en {tiempo_total}s. Ultimas lineas:\n" + "\n".join(buffer_lineas[-15:])
        )

    return False, max_reintentos  # inalcanzable, el loop siempre retorna antes


def main(anio_inicio: int, anio_fin: int, max_reintentos: int, confirmar_sobrescribir: bool) -> int:
    print(f"Watchdog de carga DANE {anio_inicio}-{anio_fin} -- liveness_timeout={LIVENESS_TIMEOUT_S}s, "
          f"max_reintentos_por_año={max_reintentos}")
    print(f"Log de incidentes: {LOG_INCIDENTES}")

    resumen = {}
    for anio in range(anio_inicio, anio_fin + 1):
        try:
            exito, intentos = correr_anio_con_reintentos(anio, max_reintentos, confirmar_sobrescribir)
        except RuntimeError as exc:
            print(f"\nDETENIDO: {exc}")
            print(f"\nResumen parcial antes de detenerse: {resumen}")
            return 1

        resumen[anio] = {"exito": exito, "intentos": intentos}
        if not exito:
            print(f"\nDETENIDO: año {anio} agoto los {max_reintentos} reintentos por cuelgues de red repetidos.")
            print(f"Revisar {LOG_INCIDENTES} -- puede que ya no sea un corte transitorio.")
            print(f"\nResumen parcial antes de detenerse: {resumen}")
            return 1

    print(f"\n{'=' * 70}\nCompletado: {anio_fin - anio_inicio + 1} años, sin agotar reintentos en ninguno.")
    print(f"Resumen (año: intentos usados): {resumen}")
    incidentes_totales = sum(v["intentos"] - 1 for v in resumen.values())
    print(f"Total de cuelgues detectados y reintentados en toda la corrida: {incidentes_totales}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anio-inicio", type=int, required=True)
    parser.add_argument("--anio-fin", type=int, required=True)
    parser.add_argument("--max-reintentos", type=int, default=MAX_REINTENTOS_POR_ANIO_DEFAULT)
    parser.add_argument(
        "--confirmar-sobrescribir-terridata-proyeccion", action="store_true",
        dest="confirmar_sobrescribir",
        help="Se pasa tal cual a cada subproceso de actualizar_densidad_dane -- ver ese modulo.",
    )
    args = parser.parse_args()
    sys.exit(main(args.anio_inicio, args.anio_fin, args.max_reintentos, args.confirmar_sobrescribir))
