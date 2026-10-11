"""Clasificacion real / proyeccion de una fila de MapCalorDB segun su originVar.

Misma regla que techo360-proyeccion/app/origen.py (un helper por repo; mantener
sincronizados a mano):

    es_proyeccion(origen) = origen EMPIEZA por "proyeccion-"
                         o TERMINA en "-proyeccion"  (caso TerriData-proyeccion)

NO es '%proyeccion%': "DANE-proyeccion-poblacion-AAAA" es el NOMBRE de la fuente
oficial del DANE (dato real). es_real = not es_proyeccion. Una fila sin originVar
(None) no cae en ninguno de los dos patrones y se trata como real.

fiabilidad_de_origen(): fiabilidad 0-100 de una fila que YA existe en BD. Un dato
real vale 100 por convencion (no es una estimacion; la columna intervaloConfianza
es NULL en los datos reales). Una proyeccion usa su intervaloConfianza (reusado como
% 0-100, ver ingestion/db.py) y, si faltara, el minimo por defecto.
"""

PREFIJO_PROYECCION = "proyeccion-"
SUFIJO_PROYECCION = "-proyeccion"

FIABILIDAD_DATO_REAL = 100.0
FIABILIDAD_MINIMA = 30.0


def es_proyeccion(originvar) -> bool:
    if not isinstance(originvar, str):
        return False
    return originvar.startswith(PREFIJO_PROYECCION) or originvar.endswith(SUFIJO_PROYECCION)


def es_real(originvar) -> bool:
    return not es_proyeccion(originvar)


def fiabilidad_de_origen(originvar, intervalo_confianza) -> float:
    if es_real(originvar):
        return FIABILIDAD_DATO_REAL
    if intervalo_confianza is None:
        return FIABILIDAD_MINIMA
    return float(min(100.0, max(0.0, float(intervalo_confianza))))
