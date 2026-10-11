"""Dibuja en PDF (ReportLab) las escenas vectoriales de reportes_escenas.py.

La escena trae coordenadas con el origen arriba a la izquierda (como SVG); aquí se invierte el eje Y
y se escala al ancho disponible. El texto se dibuja con Helvetica o, para las plantillas académicas,
Times (más estrecha que Helvetica, que es la fuente con la que se midieron los textos)."""
from __future__ import annotations

from reportlab.lib import colors
from reportlab.platypus import Flowable


def _color(valor: str | None):
    return colors.HexColor(valor) if valor else None


class EscenaPDF(Flowable):
    def __init__(self, escena: dict, ancho_destino: float, serif: bool = False, escala_texto: float = 1.0):
        super().__init__()
        self.escena = escena
        self.escala = ancho_destino / escena["ancho"]
        self.serif = serif
        self.width = ancho_destino
        self.height = escena["alto"] * self.escala

    def wrap(self, ancho_disponible, alto_disponible):
        return self.width, self.height

    def _fuente(self, negrita: bool) -> str:
        if self.serif:
            return "Times-Bold" if negrita else "Times-Roman"
        return "Helvetica-Bold" if negrita else "Helvetica"

    def draw(self):
        lienzo = self.canv
        lienzo.saveState()
        lienzo.translate(0, self.height)
        lienzo.scale(self.escala, -self.escala)
        for e in self.escena["elementos"]:
            tipo = e["t"]
            if tipo == "text":
                self._texto(lienzo, e)
                continue
            relleno, trazo = _color(e.get("fill")), _color(e.get("stroke"))
            lienzo.saveState()
            if relleno:
                lienzo.setFillColor(relleno)
            if trazo:
                lienzo.setStrokeColor(trazo)
                lienzo.setLineWidth(e.get("sw", 1))
            if e.get("dash"):
                lienzo.setDash(e["dash"])
            if tipo == "rect":
                lienzo.rect(e["x"], e["y"], e["w"], e["h"], stroke=1 if trazo else 0, fill=1 if relleno else 0)
            elif tipo == "line":
                lienzo.setStrokeColor(_color(e["stroke"]))
                lienzo.setLineWidth(e.get("sw", 1))
                lienzo.line(e["x1"], e["y1"], e["x2"], e["y2"])
            elif tipo == "circle":
                lienzo.circle(e["cx"], e["cy"], e["r"], stroke=1 if trazo else 0, fill=1 if relleno else 0)
            elif tipo == "poly":
                puntos = e["p"]
                if puntos:
                    ruta = lienzo.beginPath()
                    ruta.moveTo(*puntos[0])
                    for punto in puntos[1:]:
                        ruta.lineTo(*punto)
                    if e.get("cerrado"):
                        ruta.close()
                    lienzo.drawPath(ruta, stroke=1 if trazo else 0, fill=1 if relleno else 0)
            lienzo.restoreState()
        lienzo.restoreState()

    def _texto(self, lienzo, e):
        lienzo.saveState()
        lienzo.translate(e["x"], e["y"])
        lienzo.scale(1, -1)  # texto derecho (el eje Y de la escena va hacia abajo)
        if e.get("rot"):
            lienzo.rotate(-e["rot"])
        lienzo.setFillColor(_color(e.get("fill", "#3d3935")))
        lienzo.setFont(self._fuente(bool(e.get("bold"))), e["size"])
        ancla = e.get("anchor", "start")
        if ancla == "middle":
            lienzo.drawCentredString(0, 0, e["s"])
        elif ancla == "end":
            lienzo.drawRightString(0, 0, e["s"])
        else:
            lienzo.drawString(0, 0, e["s"])
        lienzo.restoreState()
