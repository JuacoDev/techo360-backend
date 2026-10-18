"""Escenarios por subfamilia (5.2): eligen entidades, redactan la pregunta, piden el contexto REAL al orquestador y
arman la respuesta con las plantillas. Todo con semilla fija; las listas se recorren siempre ordenadas.

`Generador.<SUB>()` devuelve un `Candidato` o None (y cuenta el motivo en `descartes`). Los controles por par,
la deduplicacion y las cuotas viven en `generar.py`.
"""
import hashlib
import random
import re
from collections import Counter
from dataclasses import dataclass, field

from services.orquestador import alias as al
from services.orquestador.resolutor import resolver

from . import lengua as lg
from . import preguntas as pq
from . import resp_a, resp_b, resp_c, resp_def
from .datos import HOY, Datos
from .resp_comun import Eleccion, ultimo_cierre

CIUDADES = {"11001": "Bogotá", "76001": "Cali", "13001": "Cartagena", "54001": "Cúcuta"}
VARS_6 = [al.V_SCORE, al.V_ACUEDUCTO, al.V_ALCANTARILLADO, al.V_DEFICIT_CUANTITATIVO, al.V_DEFICIT_CUALITATIVO, al.V_DENSIDAD]
FRONTERA_REAL = {al.V_ACUEDUCTO: 2024, al.V_ALCANTARILLADO: 2024, al.V_DENSIDAD: 2023,
                 al.V_DEFICIT_CUANTITATIVO: 2018, al.V_DEFICIT_CUALITATIVO: 2018, al.V_SCORE: 2018}
ANIO_MIN_VAR = {al.V_ACUEDUCTO: 2008, al.V_ALCANTARILLADO: 2008, al.V_DENSIDAD: 2000,
                al.V_DEFICIT_CUANTITATIVO: 2018, al.V_DEFICIT_CUALITATIVO: 2018, al.V_SCORE: 2018}
SIN_DATO_REAL = ("27493", "88001", "94343")  # municipios sin ningun dato real (COMPROBADO en el inventario)
DEPARTAMENTOS_AGREGADO = ["Antioquia", "Santander", "Cundinamarca", "Tolima", "Huila", "Atlántico", "Risaralda", "Quindío", "Casanare", "Cesar"]
NOMBRES_FALSOS = ["Bucaramnga", "Medelin", "Barranquila", "Cartajena", "Floridablanka", "Villa Esperanza del Mar", "Puerto Alegre Nuevo",
                  "Santa Clara de los Montes", "Quito", "Lima", "Monteverde Alto", "Pereyra", "Manizalez", "Bukaramanga", "Villavisencio",
                  "Rionegru", "Sabanagrande del Norte", "Valledupar Viejo", "Tumaco Alto", "Palmirra", "Sincelejoo", "Pastto", "Neivaa",
                  "Girardotte", "Cienaga Verde", "Lorica Nueva", "Armeniaa", "Yopalito", "Montería Vieja", "Cartagena Nueva", "Tunjaa",
                  "Mocoaa", "Quibdoo", "Popayanes", "Bucaramanguita", "Chiquinquirá Alta", "Zipaquirito", "Fusagasugá Nueva", "Ocañita",
                  "Sogamosito", "Duitamita", "Pitalitoo", "Leticiaa", "Arauquita Nueva", "Barrancabermejita", "Santa Marta Vieja"]


@dataclass
class Candidato:
    familia: str
    subfamilia: str
    entrada: dict
    user: str
    respuesta: str
    plantilla_p: str
    plantilla_r: str
    meta: dict = field(default_factory=dict)
    ctx: object = None      # contexto leido (no se serializa)
    render: dict = None     # salida de Datos.renderizar (no se serializa)


class Generador:
    def __init__(self, datos: Datos, semilla: int, reservados: set[str], modo: str = "train", b3_con_cifras: bool = False):
        self.d = datos
        self.semilla = semilla
        self.modo = modo
        self.reservados = set(reservados)
        resp_b.B3_CON_CIFRAS = b3_con_cifras
        todos = sorted(datos.universo)
        usables = [c for c in todos if c not in SIN_DATO_REAL]
        self.pool = [c for c in usables if (c in self.reservados) == (modo == "holdout")]
        self.por_depto: dict[str, list[str]] = {}
        for c in self.pool:
            self.por_depto.setdefault(datos.municipio(c)["departamento"], []).append(c)
        cat = datos.catalogo
        self.homonimos = {k: [m.divipola for m in v] for k, v in sorted(cat.por_nombre.items())
                          if len(v) > 1 and k not in ("cali", "bogota", "bogota dc", "bogota d c", "cartagena", "cucuta")}
        self.nombre_hom = {c for v in self.homonimos.values() for c in v}
        self.sensibles = {k: [m.divipola for m in cat.por_nombre.get(k, [])] for k in sorted(al.NOMBRES_SENSIBLES)}
        self.cod_sensibles = {c for v in self.sensibles.values() for c in v}
        self.falsos = [x for x in NOMBRES_FALSOS if self._es_falso(x)]
        self._rng: dict[str, random.Random] = {}
        self._el: dict[str, Eleccion] = {}
        self.descartes: Counter = Counter()
        self.ternas: Counter = Counter()
        self.cuenta_var: Counter = Counter()
        self.generados: Counter = Counter()

    # ------------------------------------------------------------------ infraestructura
    def _es_falso(self, nombre: str) -> bool:
        """Un nombre sirve como «municipio inexistente» solo si el resolutor real no lo toma por ninguno."""
        r = resolver(f"cobertura de acueducto en {nombre} 2022", self.d.catalogo, HOY)
        return not r.municipios and r.aclaracion is None

    def rng(self, sub: str) -> random.Random:
        if sub not in self._rng:
            self._rng[sub] = random.Random(f"{self.semilla}:{self.modo}:{sub}")
        return self._rng[sub]

    def el(self, sub: str) -> Eleccion:
        if sub not in self._el:
            self._el[sub] = Eleccion(self.rng(sub + ":plantillas"))
        return self._el[sub]

    def descarta(self, sub: str, motivo: str) -> None:
        self.descartes[(sub, motivo)] += 1

    def terna_ok(self, codigo: str, variable: str, anio) -> bool:
        return self.ternas[(codigo, variable, anio)] < 3

    def terna_usa(self, codigo: str, variable: str, anio) -> None:
        self.ternas[(codigo, variable, anio)] += 1

    def var_equilibrada(self, rng, opciones=None, familia="A"):
        opciones = opciones or VARS_6
        minimo = min(self.cuenta_var[(familia, v)] for v in opciones)
        candidatas = [v for v in opciones if self.cuenta_var[(familia, v)] == minimo]
        v = rng.choice(candidatas)
        self.cuenta_var[(familia, v)] += 1
        return v

    def muni(self, sub: str, excluir=()) -> str:
        """Departamento por turnos (bolsa barajada) y municipio al azar dentro de el."""
        deptos = sorted(self.por_depto)
        _, dep = self.el(sub + ":dep").de("dep", deptos)
        opciones = [c for c in self.por_depto[dep] if c not in excluir]
        return self.rng(sub).choice(opciones) if opciones else self.rng(sub).choice([c for c in self.pool if c not in excluir])

    def munis(self, sub: str, k: int, mismo_depto: bool) -> list[str]:
        rng = self.rng(sub)
        if mismo_depto:
            deptos = sorted(d for d, v in self.por_depto.items() if len(v) >= k)
            if deptos:
                dep = rng.choice(deptos)
                return sorted(rng.sample(self.por_depto[dep], k))
        elegidos: list[str] = []
        while len(elegidos) < k:
            c = self.muni(sub, excluir=elegidos)
            if c not in elegidos:
                elegidos.append(c)
        return elegidos

    # ------------------------------------------------------------------ redaccion de la pregunta
    def mencion(self, codigo: str, sub: str, alias: bool = False, departamento: bool = False) -> tuple[str, bool]:
        """-> (texto del municipio en la pregunta, es_sensible). Los homonimos llevan siempre su departamento."""
        m = self.d.municipio(codigo)
        rng = self.rng(sub + ":mencion")
        nombre = lg.nombre_natural(m["nombre"])
        sensible = codigo in self.cod_sensibles
        if alias and codigo in CIUDADES:
            nombre = CIUDADES[codigo]
        elif codigo in CIUDADES and rng.random() < 0.7:
            nombre = CIUDADES[codigo]
        if codigo in self.nombre_hom or departamento:
            dep = lg.nombre_natural(m["departamento"])
            if codigo == "11001":
                return nombre, sensible
            forma = rng.choice(["{n} ({d})", "{n}, {d}", "{n} de {d}"])
            return forma.format(n=nombre, d=dep), sensible
        return nombre, sensible

    def frase_var(self, variable: str, sub: str) -> tuple[str, str]:
        return self.rng(sub + ":var").choice(lg.PREGUNTA_VARIABLE[variable])

    def frases_vars(self, variables: list[str], sub: str) -> str:
        return lg.unir([self.frase_var(v, sub)[1] for v in variables])

    PASADO = re.compile(r"\b(fue|estaba|tuvo|era|ascend[ií]a|le fue|tenía|estuvo|reg[ií]str[oó])\b|¿cómo les fue|cómo le fue")

    def preguntar(self, sub: str, frames: list[str], informal_ok: bool = True, envolver: bool = True, **slots) -> tuple[str, str]:
        el, rng = self.el(sub), self.rng(sub + ":q")
        anio = slots.get("Y")
        if isinstance(anio, int) and anio >= 2026:
            frames = [f for f in frames if not self.PASADO.search(f)]  # un anio futuro no se pregunta en pasado
        i, frame = el.de("q:" + frames[0] + str(len(frames)), frames)
        wi = we = 0
        base = frame
        if envolver:
            wi, pre = el.de("wi:" + sub, pq.ENVOLTURAS_INICIO)
            we, suf = el.de("we:" + sub, pq.ENVOLTURAS_FIN)
            if pre and not base.startswith("{"):
                base = base[0].lower() + base[1:] if base[0] != "¿" else "¿" + base[1].lower() + base[2:]
            texto = pre + base.format(**slots) + suf
        else:
            texto = base.format(**slots)
        texto = lg.cap1(texto)
        if informal_ok and rng.random() < 0.15:
            texto = lg.informal(texto)
        return texto, f"{sub}.q{i:02d}{hashlib.md5(frames[0].encode()).hexdigest()[:2]}.w{wi}{we}"

    # ------------------------------------------------------------------ armado comun
    def entrada(self, pregunta, munis, variables, anios, implicito, huecos=None) -> dict:
        e = {"pregunta": pregunta, "municipios": list(munis), "variables": list(variables), "anios": list(anios),
             "anio_implicito": bool(implicito)}
        if huecos:
            e["huecos"] = huecos
        return e

    def paridad(self, entrada: dict) -> str | None:
        """El resolutor REAL, ante la misma pregunta, llega a la misma entrada (si no, produccion no armaria este contexto)."""
        r = resolver(entrada["pregunta"], self.d.catalogo, HOY)
        if r.aclaracion is not None:
            return f"resolutor_aclara_{r.aclaracion['tipo']}"
        if [m["divipola"] for m in r.municipios] != list(entrada["municipios"]):
            return "resolutor_municipios"
        if list(r.variables) != list(entrada["variables"]):
            return "resolutor_variables"
        if list(r.anios) != list(entrada["anios"]) or bool(r.anio_implicito) != bool(entrada["anio_implicito"]):
            return "resolutor_anios"
        return None

    def armar(self, familia, sub, entrada, qid, builder, exige=None, paridad=True, meta=None) -> Candidato | None:
        if paridad:
            motivo = self.paridad(entrada)
            if motivo:
                self.descarta(sub, "gen:" + motivo)
                return None
        render = self.d.renderizar(entrada)
        ctx = lg.leer_contexto(render, self.d)
        if exige:
            motivo = exige(ctx)
            if motivo:
                self.descarta(sub, "gen:" + motivo)
                return None
        texto, rid = builder(ctx, self.el(sub))
        texto = ultimo_cierre(texto)
        meta = dict(meta or {})
        meta.update({"lineas": len(ctx.lineas), "datos": [(x.variable, x.divipola, x.anio, x.real) for x in ctx.datos],
                     "sin_datos": [(x.variable, x.anio, x.motivo) for x in ctx.sin_datos], "omitidos": ctx.omitidos})
        self.generados[sub] += 1
        return Candidato(familia, sub, entrada, render["user"], texto, qid, rid, meta, ctx, render)

    # ------------------------------------------------------------------ elecciones de anio
    def _anio_real(self, rng, codigo, variable):
        lista = self.d.anios_reales(codigo, variable)
        return rng.choice(lista) if lista else None

    def _anio_proy(self, rng, codigo, variable):
        if variable in (al.V_ACUEDUCTO, al.V_ALCANTARILLADO):
            huecos = [a for a, r in self.d.huecos_naturales().get((variable, codigo), []) if r == "calculado"]
            if huecos and rng.random() < 0.3:
                return rng.choice(huecos)
            lista = self.d.anios_proyeccion(codigo, variable)
        elif variable in (al.V_DEFICIT_CUANTITATIVO, al.V_DEFICIT_CUALITATIVO):
            if rng.random() < 0.4:
                return rng.choice(range(2019, 2024))
            lista = self.d.anios_proyeccion(codigo, variable)
        elif variable == al.V_SCORE:
            lista = self.d.anios_proyeccion(codigo, variable)
        else:
            lista = self.d.anios_proyeccion(codigo, variable)
        return rng.choice(lista) if lista else None

    def _ventana(self, rng, variable, n, tipo):
        """n anios consecutivos: 'mixta' cruza la frontera real/proyeccion; 'real' queda antes; 'proy' despues."""
        b = FRONTERA_REAL[variable]
        minimo = ANIO_MIN_VAR[variable]
        if tipo == "mixta":
            ini = rng.randint(max(minimo, b - (n - 2)), b)
        elif tipo == "real":
            ini = rng.randint(minimo, max(minimo, b - n + 1))
        else:
            ini = rng.randint(b + 1, 2043 - n)
        fin = min(ini + n - 1, 2042)
        return list(range(ini, fin + 1))

    @staticmethod
    def _slots_anios(anios: list[int], rng, forzar_lista=False) -> dict:
        consecutivos = anios == list(range(anios[0], anios[-1] + 1))
        return {"Y1": anios[0], "Y2": anios[-1], "YS": lg.unir([str(a) for a in anios]), "consecutivos": consecutivos and not forzar_lista}

    # ------------------------------------------------------------------ A1
    def A1(self):
        sub, rng = "A1", self.rng("A1")
        variable = self.var_equilibrada(rng)
        for _ in range(60):
            c = self.muni(sub)
            anio = self._anio_real(rng, c, variable)
            if anio is not None and self.terna_ok(c, variable, anio):
                break
        else:
            return self._falla(sub, "sin_municipio_con_dato_real")
        self.terna_usa(c, variable, anio)
        M, sens = self.mencion(c, sub)
        V, v = self.frase_var(variable, sub)
        p, qid = self.preguntar(sub, pq.PUNTUAL, informal_ok=not sens, M=M, V=V, v=v, Y=anio)
        e = self.entrada(p, [c], [variable], [anio], False)
        return self.armar("A", sub, e, qid, resp_a.a1, lambda x: None if len(x.datos) == 1 and x.datos[0].real and not x.sin_datos else "forma_a1",
                          meta={"variable": variable})

    def _falla(self, sub, motivo):
        self.descarta(sub, "gen:" + motivo)
        return None

    # ------------------------------------------------------------------ A2
    def A2(self):
        sub, rng = "A2", self.rng("A2")
        variable = self.var_equilibrada(rng)
        for _ in range(60):
            c = self.muni(sub)
            anio = self._anio_proy(rng, c, variable)
            if anio is not None and self.terna_ok(c, variable, anio):
                break
        else:
            return self._falla(sub, "sin_municipio_con_proyeccion")
        self.terna_usa(c, variable, anio)
        M, sens = self.mencion(c, sub)
        V, v = self.frase_var(variable, sub)
        frames = pq.PUNTUAL + (pq.FUTURO if anio >= 2026 else [])
        p, qid = self.preguntar(sub, frames, informal_ok=not sens, M=M, V=V, v=v, Y=anio)
        e = self.entrada(p, [c], [variable], [anio], False)
        return self.armar("A", sub, e, qid, resp_a.a2, lambda x: None if len(x.datos) == 1 and not x.datos[0].real and not x.sin_datos else "forma_a2",
                          meta={"variable": variable})

    # ------------------------------------------------------------------ A3
    def A3(self, tamano="medio"):
        sub, rng = "A3", self.rng("A3")
        if tamano == "chico":
            nvars, n = 1, rng.choice([2, 3])
        elif tamano == "medio":
            nvars, n = (1, rng.choice([4, 5, 6])) if rng.random() < 0.65 else (2, rng.choice([2, 3, 4]))
        else:
            nvars, n = (2, 6) if rng.random() < 0.6 else (2, 5)
        variables = [self.var_equilibrada(rng)]
        while len(variables) < nvars:
            extra = [v for v in VARS_6 if v not in variables]
            variables.append(self.var_equilibrada(rng, extra))
        tipo = rng.choices(["mixta", "real", "proy"], weights=[35, 50, 15])[0]
        tipo_vars = {tipo}
        if any(v in (al.V_DEFICIT_CUANTITATIVO, al.V_DEFICIT_CUALITATIVO, al.V_SCORE) for v in variables) and tipo == "real":
            tipo = "mixta"
        for _ in range(60):
            c = self.muni(sub)
            anios = self._ventana(rng, variables[0], n, tipo)
            if len(variables) > 1:
                piso = max(ANIO_MIN_VAR[v] for v in variables)
                anios = [a for a in anios if a >= piso]
                if len(anios) < min(n, 2):
                    continue
            if all(self.terna_ok(c, v, a) for v in variables for a in anios):
                break
        else:
            return self._falla(sub, "sin_ventana")
        for v in variables:
            for a in anios:
                self.terna_usa(c, v, a)
        M, sens = self.mencion(c, sub)
        s = self._slots_anios(anios, rng, forzar_lista=rng.random() < 0.3 and len(anios) >= 3)
        if len(variables) == 1:
            V, vv = self.frase_var(variables[0], sub)
            frames = pq.SERIE_RANGO if s["consecutivos"] else pq.SERIE_LISTA
            p, qid = self.preguntar(sub, frames, informal_ok=not sens, M=M, V=V, v=vv, Y1=s["Y1"], Y2=s["Y2"], YS=s["YS"])
        else:
            frames = pq.SERIE_MULTI_RANGO if s["consecutivos"] else pq.SERIE_MULTI_LISTA
            p, qid = self.preguntar(sub, frames, informal_ok=not sens, M=M, Vs=self.frases_vars(variables, sub),
                                    Y1=s["Y1"], Y2=s["Y2"], YS=s["YS"])
        e = self.entrada(p, [c], variables, anios, False)
        deriv = len(variables) == 1 and rng.random() < 0.18
        esperado = len(variables) * len(anios)
        return self.armar("A", sub, e, qid, lambda x, el: resp_a.a3(x, el, con_derivado=deriv),
                          lambda x: None if len(x.datos) == esperado and not x.sin_datos else "faltan_datos_a3",
                          meta={"variables": variables, "derivado": deriv})

    # ------------------------------------------------------------------ A4
    def A4(self, tamano="chico"):
        sub, rng = "A4", self.rng("A4")
        if tamano == "chico":
            k, na = rng.choice([2, 3]), 1
        elif tamano == "medio":
            k, na = ((rng.choice([4, 5]), 1) if rng.random() < 0.55 else (rng.choice([2, 3]), 2))
        else:
            k, na = ((5, 2) if rng.random() < 0.5 else (3, 3))
        variable = self.var_equilibrada(rng)
        kind = rng.choices(["real", "proy"], weights=[78, 22])[0]
        if na > 1 and variable in (al.V_DEFICIT_CUANTITATIVO, al.V_DEFICIT_CUALITATIVO, al.V_SCORE):
            kind = "proy"  # solo hay un anio real (2018)
        for _ in range(60):
            cs = self.munis(sub, k, mismo_depto=rng.random() < 0.5)
            if kind == "real":
                comunes = set.intersection(*[set(self.d.anios_reales(c, variable)) for c in cs])
                pool = sorted(comunes)
            else:
                pool = list(range(max(2025, ANIO_MIN_VAR[variable]), 2043))
            if len(pool) < na:
                continue
            if na == 1:
                anios = [rng.choice(pool)]
            else:
                ini = rng.randrange(0, len(pool) - na + 1)
                anios = pool[ini:ini + na] if rng.random() < 0.6 else sorted(rng.sample(pool, na))
            if all(self.terna_ok(c, variable, a) for c in cs for a in anios):
                break
        else:
            return self._falla(sub, "sin_municipios_comparables")
        for c in cs:
            for a in anios:
                self.terna_usa(c, variable, a)
        menciones = [self.mencion(c, sub) for c in cs]
        sens = any(s for _, s in menciones)
        Ms = lg.unir([m for m, _ in menciones])
        Mo = lg.unir([m for m, _ in menciones], "o")
        V, v = self.frase_var(variable, sub)
        if na == 1:
            p, qid = self.preguntar(sub, pq.COMPARA, informal_ok=not sens, Ms=Ms, Mo=Mo, V=V, v=v, Y=anios[0])
        else:
            p, qid = self.preguntar(sub, pq.COMPARA_ANIOS, informal_ok=not sens, Ms=Ms, V=V, v=v, YS=lg.unir([str(a) for a in anios]))
        e = self.entrada(p, cs, [variable], anios, False)
        dif = na == 1 and rng.random() < 0.3
        return self.armar("A", sub, e, qid, lambda x, el: resp_a.a4(x, el, con_diferencia=dif),
                          lambda x: None if len(x.datos) == k * na and not x.sin_datos else "faltan_datos_a4",
                          meta={"variable": variable, "k": k, "derivado": dif})

    # ------------------------------------------------------------------ A5
    def A5(self, tamano="medio"):
        sub, rng = "A5", self.rng("A5")
        if tamano == "chico":
            nv, na = rng.choice([2, 3]), 1
        elif tamano == "medio":
            nv, na = ((rng.choice([4, 4, 5]), 1) if rng.random() < 0.7 else (rng.choice([2, 3]), 2))
        else:
            nv, na = ((5, 2) if rng.random() < 0.4 else (rng.choice([3, 4]), 3))
        for _ in range(60):
            c = self.muni(sub)
            if na == 1:
                anios = [rng.choice([2012, 2015, 2017, 2018, 2018, 2019, 2020, 2021, 2022, 2023, 2023, 2024, 2024, 2026, 2030])]
            else:
                ini = rng.choice([2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2027])
                anios = list(range(ini, ini + na))
            if anios[0] < 2018:  # antes de 2018 solo hay coberturas y densidad
                disponibles = [al.V_ACUEDUCTO, al.V_ALCANTARILLADO, al.V_DENSIDAD]
                variables = []
                while len(variables) < min(nv, 3):
                    variables.append(self.var_equilibrada(rng, [v for v in disponibles if v not in variables]))
            else:
                variables = []
                if rng.random() < 0.55:
                    variables.append(al.V_SCORE)
                    self.cuenta_var[("A", al.V_SCORE)] += 1
                while len(variables) < nv:
                    extra = [v for v in VARS_6 if v not in variables and v != al.V_SCORE]
                    variables.append(self.var_equilibrada(rng, extra))
            rng.shuffle(variables)
            if all(self.terna_ok(c, v, a) for v in variables for a in anios):
                break
        else:
            return self._falla(sub, "sin_perfil")
        for v in variables:
            for a in anios:
                self.terna_usa(c, v, a)
        M, sens = self.mencion(c, sub)
        if na == 1:
            p, qid = self.preguntar(sub, pq.PERFIL, informal_ok=not sens, M=M, Vs=self.frases_vars(variables, sub), Y=anios[0])
        else:
            p, qid = self.preguntar(sub, pq.PERFIL_ANIOS, informal_ok=not sens, M=M, Vs=self.frases_vars(variables, sub),
                                    YS=lg.unir([str(a) for a in anios]))
        e = self.entrada(p, [c], variables, anios, False)
        return self.armar("A", sub, e, qid, resp_a.a5,
                          lambda x: None if len(x.datos) == len(variables) * na and not x.sin_datos else "faltan_datos_a5",
                          meta={"variables": variables})

    # ------------------------------------------------------------------ A6
    def A6(self, forma="uno"):
        sub, rng = "A6", self.rng("A6")
        sin_score = [v for v in VARS_6 if v != al.V_SCORE]
        if forma == "uno":
            variables, k = [self.var_equilibrada(rng, sin_score)], 1
        elif forma == "vars":
            variables = [self.var_equilibrada(rng, sin_score)]
            while len(variables) < rng.choice([2, 3]):
                variables.append(self.var_equilibrada(rng, [v for v in sin_score if v not in variables]))
            k = 1
        else:
            variables, k = [self.var_equilibrada(rng, sin_score)], rng.choice([2, 3])
        for _ in range(60):
            cs = self.munis(sub, k, mismo_depto=False) if k > 1 else [self.muni(sub)]
            if all(self.terna_ok(c, v, None) for c in cs for v in variables):
                break
        else:
            return self._falla(sub, "sin_municipio")
        for c in cs:
            for v in variables:
                self.terna_usa(c, v, None)
        if k == 1:
            M, sens = self.mencion(cs[0], sub)
            if len(variables) == 1:
                V, vv = self.frase_var(variables[0], sub)
                p, qid = self.preguntar(sub, pq.IMPLICITO, informal_ok=not sens, M=M, V=V, v=vv)
            else:
                p, qid = self.preguntar(sub, pq.IMPLICITO_VARIOS, informal_ok=not sens, M=M, Vs=self.frases_vars(variables, sub))
        else:
            menciones = [self.mencion(c, sub) for c in cs]
            sens = any(s for _, s in menciones)
            V, vv = self.frase_var(variables[0], sub)
            p, qid = self.preguntar(sub, pq.IMPLICITO_MUNICIPIOS, informal_ok=not sens, Ms=lg.unir([m for m, _ in menciones]), V=V, v=vv)
        e = self.entrada(p, cs, variables, [], True)
        return self.armar("A", sub, e, qid, resp_a.a6,
                          lambda x: None if len(x.datos) == len(cs) * len(variables) and not x.sin_datos and all(d.implicito and d.real for d in x.datos) else "forma_a6",
                          meta={"variables": variables})

    # ------------------------------------------------------------------ A7
    def A7(self):
        sub, rng = "A7", self.rng("A7")
        for _ in range(100):
            k, nv, n = rng.choice([3, 4, 5]), rng.choice([2, 2, 3]), rng.choice([3, 4])
            if 22 <= k * nv * n <= 40:
                break
        ini = rng.choice([2013, 2016, 2018, 2019, 2020, 2021, 2022, 2023])
        variables = rng.sample([al.V_ACUEDUCTO, al.V_ALCANTARILLADO, al.V_DENSIDAD] if ini < 2018 else VARS_6, nv if ini >= 2018 else min(nv, 3))
        for v in variables:
            self.cuenta_var[("A", v)] += 1
        anios = list(range(ini, ini + n))
        cs = self.munis(sub, k, mismo_depto=rng.random() < 0.4)
        menciones = [self.mencion(c, sub) for c in cs]
        sens = any(s for _, s in menciones)
        s = self._slots_anios(anios, rng)
        p, qid = self.preguntar(sub, pq.MUCHOS, informal_ok=not sens, Ms=lg.unir([m for m, _ in menciones]),
                                Vs=self.frases_vars(variables, sub), Y1=s["Y1"], Y2=s["Y2"], YS=s["YS"])
        if "{YS}" in p:
            return self._falla(sub, "plantilla")
        e = self.entrada(p, cs, variables, anios, False)
        return self.armar("A", sub, e, qid, resp_a.a7, lambda x: None if x.omitidos >= 1 and len(x.datos) >= 3 else "sin_omision",
                          meta={"variables": variables, "k": k})

    # ------------------------------------------------------------------ B1
    def _hueco(self, rng, motivos: tuple[str, ...], variables=None):
        """Un hueco natural (variable, municipio, anio, motivo) del pool, con su terna libre."""
        huecos = self.d.huecos_naturales()
        opciones = []
        for (variable, c), lista in sorted(huecos.items()):
            if c not in self.pool or (variables and variable not in variables):
                continue
            for a, r in lista:
                if r in motivos and 2000 <= a <= 2042:
                    opciones.append((variable, c, a, r))
        rng.shuffle(opciones)
        for variable, c, a, r in opciones:
            if self.terna_ok(c, variable, a):
                return variable, c, a, r
        return None

    def B1(self, tipo: str):
        sub, rng = "B1", self.rng("B1")
        huecos_forzados = None
        if tipo in ("sin_historia", "sin_extremo"):
            motivos = ("sin_historia",) if tipo == "sin_historia" else ("sin_extremo",)
            h = self._hueco(rng, motivos, variables=(al.V_ACUEDUCTO, al.V_ALCANTARILLADO, al.V_DENSIDAD) if tipo == "sin_historia"
                            else (al.V_DEFICIT_CUANTITATIVO, al.V_DEFICIT_CUALITATIVO))
            if h is None and tipo == "sin_extremo":
                # los municipios con extremo faltante no estan en el pool de entrenamiento: se usan fuera del pool
                h = self._hueco_fuera_de_pool(rng, "sin_extremo")
            if h is None:
                return self._falla(sub, "sin_hueco_" + tipo)
            variable, c, anio, _ = h
        elif tipo == "fuera_de_rango":
            variable = rng.choice([al.V_ACUEDUCTO, al.V_ALCANTARILLADO, al.V_DEFICIT_CUANTITATIVO, al.V_DEFICIT_CUALITATIVO])
            c = self.muni(sub)
            anio = rng.randint(2000, ANIO_MIN_VAR[variable] - 1)
            if not self.terna_ok(c, variable, anio):
                return self._falla(sub, "terna")
        elif tipo == "componente":
            variable = al.V_SCORE
            c = self.muni(sub)
            anio = rng.randint(2000, 2017)
            if not self.terna_ok(c, variable, anio):
                return self._falla(sub, "terna")
        elif tipo == "sin_dato_real":
            variable = rng.choice([al.V_ACUEDUCTO, al.V_ALCANTARILLADO, al.V_DENSIDAD, al.V_DEFICIT_CUANTITATIVO, al.V_DEFICIT_CUALITATIVO])
            candidatos = [c for c in SIN_DATO_REAL if not self.d.anios_reales(c, variable)]
            if not candidatos or self.modo == "holdout":
                return self._falla(sub, "sin_municipio_sin_dato_real")
            c = rng.choice(candidatos)
            if not self.terna_ok(c, variable, None):
                return self._falla(sub, "terna")
            anio = None
        elif tipo == "sospechoso":
            variable = rng.choice([al.V_ACUEDUCTO, al.V_ALCANTARILLADO])
            c = self.muni(sub)
            anio = rng.randint(2009, 2023)
            huecos_forzados = [{"variable": variable, "divipola": c, "anio": anio, "motivo": "valor_sospechoso"}]
        else:
            return self._falla(sub, "tipo_desconocido")
        self.terna_usa(c, variable, anio)
        M, sens = self.mencion(c, sub)
        V, v = self.frase_var(variable, sub)
        if anio is None:
            p, qid = self.preguntar(sub, pq.IMPLICITO, informal_ok=not sens, M=M, V=V, v=v)
            e = self.entrada(p, [c], [variable], [], True)
        else:
            p, qid = self.preguntar(sub, pq.PUNTUAL, informal_ok=not sens, M=M, V=V, v=v, Y=anio)
            e = self.entrada(p, [c], [variable], [anio], False, huecos_forzados)
        return self.armar("B", sub, e, qid, lambda x, el: resp_b.b1(x, el, {}),
                          lambda x: None if len(x.sin_datos) == 1 and not x.datos else "forma_b1", meta={"variable": variable, "tipo": tipo})

    def _hueco_fuera_de_pool(self, rng, motivo):
        opciones = [(v, c, a, r) for (v, c), lista in sorted(self.d.huecos_naturales().items()) for a, r in lista
                    if r == motivo and self.modo == "train" and 2000 <= a <= 2042]
        rng.shuffle(opciones)
        return next((o for o in opciones if self.terna_ok(o[1], o[0], o[2])), None)

    # ------------------------------------------------------------------ B2
    def B2(self, tipo: str):
        sub, rng = "B2", self.rng("B2")
        if tipo == "municipio":
            x = rng.choice(self.falsos)
            variable = self.var_equilibrada(rng, VARS_6, "B")
            V, v = self.frase_var(variable, sub)
            p, qid = self.preguntar(sub, pq.PUNTUAL, M=x, V=V, v=v, Y=rng.choice([2019, 2021, 2022, 2023, 2024]))
            ex = {"tipo": "municipio", "X": x}
        elif tipo == "agregado":
            variable = rng.choice(VARS_6)
            V, v = self.frase_var(variable, sub)
            p, qid = self.preguntar(sub, pq.AGREGADO, v=v, D=rng.choice(DEPARTAMENTOS_AGREGADO), Y=rng.choice([2020, 2022, 2024]))
            ex = {"tipo": "agregado", "variable": variable}
        else:
            p, qid = self.preguntar(sub, pq.VARIABLE_AJENA, informal_ok=True)
            ex = {"tipo": "ajena", "pregunta": p}
        return self._desde_resolutor("B", sub, p, qid, lambda x, el: resp_b.b2(x, el, ex), meta={"tipo": tipo},
                                     exige=lambda x: None if not x.datos and not x.sin_datos and not x.ubicaciones else "contexto_no_vacio")

    def _desde_resolutor(self, familia, sub, pregunta, qid, builder, meta=None, exige=None):
        """Para preguntas sin entidades en el contexto: la entrada es exactamente lo que resuelve el resolutor real."""
        r = resolver(pregunta, self.d.catalogo, HOY)
        if r.aclaracion is not None:
            return self._falla(sub, "resolutor_aclara_" + r.aclaracion["tipo"])
        e = self.entrada(pregunta, [m["divipola"] for m in r.municipios], r.variables, r.anios, r.anio_implicito)
        return self.armar(familia, sub, e, qid, builder, exige=exige, paridad=False, meta=meta)

    # ------------------------------------------------------------------ B3
    def B3(self, tipo: str):
        sub, rng = "B3", self.rng("B3")
        h = self._hueco(rng, ("sin_historia",), variables=(al.V_ACUEDUCTO, al.V_ALCANTARILLADO, al.V_DENSIDAD))
        if h is None:
            return self._falla(sub, "sin_hueco")
        variable, c, anio, _ = h
        self.terna_usa(c, variable, anio)
        M, sens = self.mencion(c, sub)
        if tipo == "vars":
            otras = [v for v in (al.V_ACUEDUCTO, al.V_ALCANTARILLADO, al.V_DENSIDAD, al.V_DEFICIT_CUANTITATIVO)
                     if v != variable and anio >= ANIO_MIN_VAR[v] and anio in self.d.valores(c, v)]
            if not otras:
                return self._falla(sub, "otra_variable_sin_dato")
            otra = rng.choice(otras)
            variables = [variable, otra] if rng.random() < 0.5 else [otra, variable]
            p, qid = self.preguntar(sub, pq.PERFIL, informal_ok=not sens, M=M, Vs=self.frases_vars(variables, sub), Y=anio)
            e = self.entrada(p, [c], variables, [anio], False)
        elif tipo == "anios":
            siguientes = [a for a in range(anio + 1, anio + 4) if a in self.d.valores(c, variable)]
            if not siguientes:
                return self._falla(sub, "sin_anio_con_dato")
            anios = sorted([anio, siguientes[0]])
            s = self._slots_anios(anios, rng, forzar_lista=True)
            V, vv = self.frase_var(variable, sub)
            p, qid = self.preguntar(sub, pq.SERIE_LISTA, informal_ok=not sens, M=M, V=V, v=vv, YS=s["YS"])
            e = self.entrada(p, [c], [variable], anios, False)
        else:
            c2 = self.muni(sub, excluir=[c])
            if anio not in self.d.valores(c2, variable) or c2 in self.nombre_hom:
                return self._falla(sub, "segundo_municipio_sin_dato")
            M2, sens2 = self.mencion(c2, sub)
            sens = sens or sens2
            V, vv = self.frase_var(variable, sub)
            orden = [c, c2] if rng.random() < 0.5 else [c2, c]
            ms = lg.unir([self.mencion(x, sub)[0] for x in orden])
            p, qid = self.preguntar(sub, pq.COMPARA, informal_ok=not sens, Ms=ms, Mo=ms.replace(" y ", " o "), V=V, v=vv, Y=anio)
            e = self.entrada(p, orden, [variable], [anio], False)
        return self.armar("B", sub, e, qid, lambda x, el: resp_b.b3(x, el, {}),
                          lambda x: None if x.datos and x.sin_datos else "forma_b3", meta={"tipo": tipo})

    # ------------------------------------------------------------------ B4
    def B4(self, tipo: str):
        sub, rng = "B4", self.rng("B4")
        c = self.muni(sub)
        M, sens = self.mencion(c, sub)
        if tipo == "antes":
            variable = rng.choice([al.V_ACUEDUCTO, al.V_ALCANTARILLADO, al.V_DEFICIT_CUANTITATIVO, al.V_DEFICIT_CUALITATIVO])
            anio = rng.randint(2000, ANIO_MIN_VAR[variable] - 1)
            if not self.terna_ok(c, variable, anio):
                return self._falla(sub, "terna")
            self.terna_usa(c, variable, anio)
            V, v = self.frase_var(variable, sub)
            p, qid = self.preguntar(sub, pq.PUNTUAL, informal_ok=not sens, M=M, V=V, v=v, Y=anio)
            e = self.entrada(p, [c], [variable], [anio], False)
            return self.armar("B", sub, e, qid, lambda x, el: resp_b.b4(x, el, {"tipo": "antes"}),
                              lambda x: None if len(x.sin_datos) == 1 and not x.datos else "forma_b4", meta={"tipo": tipo})
        variable = rng.choice([al.V_ACUEDUCTO, al.V_ALCANTARILLADO, al.V_DENSIDAD])
        anio_pedido = rng.choice([2043, 2045, 2050, 2055, 2060, 2075, 2100])
        if not self.terna_ok(c, variable, None):
            return self._falla(sub, "terna")
        self.terna_usa(c, variable, None)
        V, v = self.frase_var(variable, sub)
        p, qid = self.preguntar(sub, pq.FUTURO, informal_ok=not sens, M=M, V=V, v=v, Y=anio_pedido)
        e = self.entrada(p, [c], [variable], [], True)
        return self.armar("B", sub, e, qid, lambda x, el: resp_b.b4(x, el, {"tipo": "despues", "anio_pedido": anio_pedido}),
                          lambda x: None if len(x.datos) == 1 and x.datos[0].implicito and not x.sin_datos else "forma_b4d", meta={"tipo": tipo})

    # ------------------------------------------------------------------ B5
    def B5(self, tipo: str):
        sub, rng = "B5", self.rng("B5")
        insistencia = self.el(sub).de("ins", pq.INSISTENCIA)[1]
        if tipo == "hueco":
            h = self._hueco(rng, ("sin_historia",), variables=(al.V_ACUEDUCTO, al.V_ALCANTARILLADO, al.V_DENSIDAD))
            if h is None:
                return self._falla(sub, "sin_hueco")
            variable, c, anio, _ = h
            self.terna_usa(c, variable, anio)
            M, sens = self.mencion(c, sub)
            V, v = self.frase_var(variable, sub)
            base, qid = self.preguntar(sub, pq.PUNTUAL, informal_ok=False, envolver=False, M=M, V=V, v=v, Y=anio)
            p = f"{base} {insistencia}"
            e = self.entrada(p, [c], [variable], [anio], False)
            return self.armar("B", sub, e, qid + ".h", lambda x, el: resp_b.b5(x, el, {}),
                              lambda x: None if len(x.sin_datos) == 1 and not x.datos else "forma_b5", meta={"tipo": tipo})
        x = rng.choice(self.falsos)
        variable = rng.choice(VARS_6)
        V, v = self.frase_var(variable, sub)
        base, qid = self.preguntar(sub, pq.PUNTUAL, informal_ok=False, envolver=False, M=x, V=V, v=v, Y=rng.choice([2020, 2022, 2023]))
        return self._desde_resolutor("B", sub, f"{base} {insistencia}", qid + ".v", lambda xx, el: resp_b.b5(xx, el, {"X": x}),
                                     meta={"tipo": tipo}, exige=lambda xx: None if not xx.datos and not xx.sin_datos and not xx.ubicaciones else "contexto_no_vacio")

    # ------------------------------------------------------------------ C
    def C1(self):
        sub, rng = "C1", self.rng("C1")
        temas = sorted(pq.GENERAL_TEMAS)
        _, tema = self.el(sub).de("tema", temas)
        p, qid = self._pregunta_c1(tema, rng)
        concepto = {"deficit": "deficit", "acueducto": "acueducto", "alcantarillado": "alcantarillado", "pobreza": "pobreza",
                    "poblacion": "poblacion", "score": "score"}[tema]
        return self._desde_resolutor("C", sub, p, qid, lambda x, el: resp_c.c1(concepto, el), meta={"tema": tema},
                                     exige=lambda x: None if not x.datos and not x.sin_datos and not x.ubicaciones else "contexto_no_vacio")

    TERRITORIOS = ["en el país", "en todo el país", "a nivel nacional", "en el territorio nacional", "en Antioquia", "en Santander", "en Cundinamarca",
                   "en el Valle del Cauca", "en el Atlántico", "en el Tolima", "en Risaralda", "en el Huila", "en el Cesar", "en La Guajira",
                   "en Casanare", "en el Quindío", "en el Magdalena", "en el Chocó"]
    C1_FRASES = {
        "deficit": ["¿Cuántos hogares {T} viven en déficit habitacional?", "¿Cuántas viviendas le faltan {T}?",
                    "¿Qué porcentaje de hogares {T} está en déficit de vivienda?", "Dame la cifra de déficit habitacional {T}."],
        "acueducto": ["¿Cuántas personas {T} no tienen acueducto?", "¿Cuál es la cobertura de acueducto {T}?",
                      "¿Qué porcentaje de hogares {T} tiene acueducto?"],
        "alcantarillado": ["¿Cuántas viviendas {T} no tienen alcantarillado?", "¿Cuál es la cobertura de alcantarillado {T}?",
                           "¿Qué porcentaje de hogares {T} tiene alcantarillado?"],
        "pobreza": ["¿Cuántas personas {T} viven en pobreza multidimensional?", "¿Cuál es la incidencia del IPM {T}?",
                    "¿Qué porcentaje de la población {T} es pobre?"],
        "poblacion": ["¿Cuántos habitantes hay {T}?", "¿Cuál es la densidad poblacional {T}?", "¿Cuántas personas viven {T}?"],
        "score": ["¿Cuál es el score de privación {T}?", "¿Cuál es el índice de privación promedio {T}?", "¿Cómo está el score de privación {T}?"],
    }

    def _pregunta_c1(self, tema, rng):
        fijas = pq.GENERAL_TEMAS[tema]
        if rng.random() < 0.45:
            return self.preguntar("C1", fijas)
        frases = self.C1_FRASES[tema]
        return self.preguntar("C1", [f.replace("{T}", "{T}") for f in frases], T=rng.choice(self.TERRITORIOS))

    def C2(self):
        sub, rng = "C2", self.rng("C2")
        palabras = sorted(pq.PALABRAS_COMUNES)
        _, palabra = self.el(sub).de("palabra", palabras)
        p, qid = self.preguntar(sub, pq.PALABRAS_COMUNES[palabra], informal_ok=False, envolver=False)
        clave = "sucre_dep" if palabra == "sucre" and "departamento" in p.lower() else palabra
        return self._desde_resolutor("C", sub, p, qid, lambda x, el: resp_c.c2(clave, el), meta={"palabra": palabra},
                                     exige=lambda x: None if not x.datos and not x.sin_datos and not x.ubicaciones else "contexto_no_vacio")

    def C3(self):
        sub, rng = "C3", self.rng("C3")
        temas = sorted(resp_c.C3_TEMAS)
        _, tema = self.el(sub).de("tema", temas)
        preguntas = resp_c.C3_TEMAS[tema][0]
        usa_y = any("{Y}" in q for q in preguntas)
        anio = rng.randint(2027, 2042) if tema == "proy_confiar" else rng.choice([2027, 2030, 2033, 2035, 2038, 2040]) if usa_y else None
        p, qid = self.preguntar(sub, preguntas, informal_ok=not usa_y, Y=anio)
        return self._desde_resolutor("C", sub, p, qid, lambda x, el: resp_c.c3(tema, anio, el), meta={"tema": tema},
                                     exige=lambda x: None if not x.datos and not x.sin_datos and not x.ubicaciones else "contexto_no_vacio")

    # ------------------------------------------------------------------ D
    def _dato_basico(self, sub, rng, codigos, real: bool | None):
        variable = self.var_equilibrada(rng, VARS_6, "D")
        c = rng.choice(codigos)
        if real is None:
            real = rng.random() < 0.6
        anio = self._anio_real(rng, c, variable) if real else self._anio_proy(rng, c, variable)
        return variable, c, anio

    def D1(self):
        sub, rng = "D1", self.rng("D1")
        codigos = [c for c in self.pool if c not in self.nombre_hom and c not in CIUDADES and c not in self.cod_sensibles]
        for _ in range(40):
            variable, c, anio = self._dato_basico(sub, rng, codigos, None)
            if anio is not None and self.terna_ok(c, variable, anio):
                break
        else:
            return self._falla(sub, "sin_dato")
        self.terna_usa(c, variable, anio)
        M, sens = self.mencion(c, sub)
        V, v = self.frase_var(variable, sub)
        p, qid = self.preguntar(sub, pq.DEPARTAMENTO_Y_DATO, informal_ok=True, M=M, V=V, v=v, Y=anio)
        e = self.entrada(p, [c], [variable], [anio], False)
        return self.armar("D", sub, e, qid, resp_def.d1, lambda x: None if len(x.datos) == 1 and not x.sin_datos else "forma_d1")

    def D2(self):
        sub, rng = "D2", self.rng("D2")
        opciones = sorted(c for c in self.cod_sensibles if c in self.pool)
        if not opciones:
            return self._falla(sub, "sin_sensibles_en_pool")
        for _ in range(40):
            variable, c, anio = self._dato_basico(sub, rng, opciones, None)
            if anio is not None and self.terna_ok(c, variable, anio):
                break
        else:
            return self._falla(sub, "sin_dato")
        self.terna_usa(c, variable, anio)
        M, _ = self.mencion(c, sub, departamento=self.d.municipio(c)["nombre"].lower() in al.NOMBRES_SENSIBLES and c in self.nombre_hom)
        V, v = self.frase_var(variable, sub)
        p, qid = self.preguntar(sub, pq.PUNTUAL, informal_ok=False, M=M, V=V, v=v, Y=anio)
        e = self.entrada(p, [c], [variable], [anio], False)
        return self.armar("D", sub, e, qid, resp_def.d2, lambda x: None if len(x.datos) == 1 and not x.sin_datos else "forma_d2")

    def D3(self):
        sub, rng = "D3", self.rng("D3")
        opciones = sorted(c for c in CIUDADES if c in self.pool)
        if not opciones:
            return self._falla(sub, "sin_ciudades_en_pool")
        for _ in range(40):
            variable, c, anio = self._dato_basico(sub, rng, opciones, None)
            if anio is not None and self.terna_ok(c, variable, anio):
                break
        else:
            return self._falla(sub, "sin_dato")
        self.terna_usa(c, variable, anio)
        alias = CIUDADES[c]
        V, v = self.frase_var(variable, sub)
        p, qid = self.preguntar(sub, pq.PUNTUAL, informal_ok=True, M=alias, V=V, v=v, Y=anio)
        e = self.entrada(p, [c], [variable], [anio], False)
        return self.armar("D", sub, e, qid, lambda x, el: resp_def.d3(x, el, alias), lambda x: None if len(x.datos) == 1 and not x.sin_datos else "forma_d3")

    def D4(self, tipo: str):
        sub, rng = "D4", self.rng("D4")
        codigos = [c for c in self.pool if c not in self.nombre_hom and c not in CIUDADES]
        c = rng.choice(codigos)
        M, sens = self.mencion(c, sub)
        frames = pq.GEO if tipo == "geo" else pq.HABLAME
        p, qid = self.preguntar(sub, frames, informal_ok=not sens, M=M)
        e = self.entrada(p, [c], [], [], True)
        return self.armar("D", sub, e, qid + "." + tipo, lambda x, el: resp_def.d4(x, el, tipo),
                          lambda x: None if len(x.ubicaciones) == 1 and not x.datos else "forma_d4", meta={"tipo": tipo})

    # ------------------------------------------------------------------ E
    def _grupo(self, sub, rng):
        claves = [k for k, v in sorted(self.homonimos.items()) if all(c in self.pool for c in v)] if self.modo == "holdout" else \
                 [k for k, v in sorted(self.homonimos.items()) if sum(c in self.pool for c in v) >= 2]
        _, k = self.el(sub).de("grupo", claves)
        return k, [c for c in self.homonimos[k] if c in self.pool]

    def E1(self):
        sub, rng = "E1", self.rng("E1")
        for _ in range(40):
            k, miembros = self._grupo(sub, rng)
            a, b = sorted(rng.sample(miembros, 2))
            variable = self.var_equilibrada(rng, VARS_6, "E")
            comunes = sorted(set(self.d.anios_reales(a, variable)) & set(self.d.anios_reales(b, variable))) if rng.random() < 0.6 else \
                      list(range(2025, 2043))
            if not comunes:
                continue
            anio = rng.choice(comunes)
            if self.terna_ok(a, variable, anio) and self.terna_ok(b, variable, anio):
                break
        else:
            return self._falla(sub, "sin_par")
        self.terna_usa(a, variable, anio)
        self.terna_usa(b, variable, anio)
        orden = [a, b] if rng.random() < 0.5 else [b, a]
        MD1, MD2 = (self.mencion(c, sub, departamento=True)[0] for c in orden)
        V, v = self.frase_var(variable, sub)
        p, qid = self.preguntar(sub, pq.HOMONIMOS, informal_ok=False, MD1=MD1, MD2=MD2, V=V, v=v, Y=anio)
        e = self.entrada(p, orden, [variable], [anio], False)
        dif = rng.random() < 0.18
        return self.armar("E", sub, e, qid, lambda x, el: resp_def.e1(x, el, con_diferencia=dif),
                          lambda x: None if len(x.datos) == 2 and not x.sin_datos else "forma_e1", meta={"variable": variable})

    def E2(self):
        sub, rng = "E2", self.rng("E2")
        for _ in range(40):
            k, miembros = self._grupo(sub, rng)
            c = rng.choice(miembros)
            variable = self.var_equilibrada(rng, VARS_6, "E")
            anio = self._anio_real(rng, c, variable) if rng.random() < 0.55 else self._anio_proy(rng, c, variable)
            if anio is not None and self.terna_ok(c, variable, anio):
                break
        else:
            return self._falla(sub, "sin_dato")
        self.terna_usa(c, variable, anio)
        MD, _ = self.mencion(c, sub, departamento=True)
        V, v = self.frase_var(variable, sub)
        p, qid = self.preguntar(sub, pq.PUNTUAL, informal_ok=False, M=MD, V=V, v=v, Y=anio)
        e = self.entrada(p, [c], [variable], [anio], False)
        return self.armar("E", sub, e, qid, resp_def.e2, lambda x: None if len(x.datos) == 1 and not x.sin_datos else "forma_e2")

    # ------------------------------------------------------------------ F
    def F(self, tipo: str):
        sub, rng = "F", self.rng("F")
        el = self.el(sub)
        if tipo == "pura":
            ip, p = el.de("pura", pq.INYECCION_PURA)
            secreto = any(t in p.lower() for t in ("instrucciones", "system prompt", "texto que aparece", "reglas de seguridad"))
            return self._desde_resolutor("F", sub, p, f"F.pura{ip:02d}", lambda x, e: resp_def.f_pura(e, secreto), meta={"tipo": tipo},
                                         exige=lambda x: None if not x.datos and not x.ubicaciones else "contexto_con_datos")
        variable = self.var_equilibrada(rng, VARS_6, "F")
        for _ in range(40):
            c = self.muni(sub)
            anio = self._anio_real(rng, c, variable)
            if anio is not None and self.terna_ok(c, variable, anio):
                break
        else:
            return self._falla(sub, "sin_dato")
        self.terna_usa(c, variable, anio)
        M, sens = self.mencion(c, sub)
        V, v = self.frase_var(variable, sub)
        legit, _ = self.preguntar(sub, pq.PUNTUAL, informal_ok=False, envolver=False, M=M, V=V, v=v, Y=anio)
        m = self.d.municipio(c)
        if rng.random() < 0.3:
            il, plantilla = el.de("lf", pq.INYECCION_LINEA_FALSA)
            p = plantilla.format(Vn=al.NOMBRE_VARIABLE[variable], Mn=m["nombre"], Dn=m["departamento"], Yn=anio, P=legit)
            qid = f"F.linea{il}"
        else:
            i, plantilla = el.de("inj", pq.INYECCION)
            p = plantilla.format(V=lg.var_frase(variable), M=M, P=legit)
            qid = f"F.inj{i:02d}"
        secreto = "instrucciones internas" in p.lower()
        e = self.entrada(p, [c], [variable], [anio], False)
        return self.armar("F", sub, e, qid, lambda x, ee: resp_def.f_con_dato(x, ee, secreto),
                          lambda x: None if len(x.datos) == 1 and not x.sin_datos else "forma_f", meta={"tipo": tipo, "variable": variable})
