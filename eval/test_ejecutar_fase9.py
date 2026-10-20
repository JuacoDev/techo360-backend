import json
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from eval.ejecutar_fase9 import ejecutar

class StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        longitud = int(self.headers["Content-Length"])
        json.loads(self.rfile.read(longitud))
        cuerpo = json.dumps({"message": {"content": "No dispongo de ese dato."}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *_):
        pass

class EjecutarFase9Test(unittest.TestCase):
    def test_servidor_simulado_guarda_respuesta_cruda(self):
        servidor = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        hilo = Thread(target=servidor.serve_forever, daemon=True)
        hilo.start()
        try:
            with tempfile.TemporaryDirectory() as temporal:
                preguntas = Path(temporal) / "preguntas.jsonl"
                salida = Path(temporal) / "resultado.json"
                preguntas.write_text(json.dumps({"id": "stub-1", "grupo": "abstencion", "historial": [], "pregunta": "Consulta sin cobertura", "esperado": {"tipo": "abstencion"}}) + "\n", encoding="utf-8")
                documento = ejecutar(f"http://127.0.0.1:{servidor.server_port}/api/chat", preguntas, salida, "stub")
                self.assertTrue(salida.exists())
                self.assertEqual(documento["resultados"][0]["respuesta_cruda"]["message"]["content"], "No dispongo de ese dato.")
                self.assertTrue(documento["resultados"][0]["evaluacion"]["abstencion_correcta"])
        finally:
            servidor.shutdown()
            servidor.server_close()
            hilo.join()

if __name__ == "__main__":
    unittest.main()