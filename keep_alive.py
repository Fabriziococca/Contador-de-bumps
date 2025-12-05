from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
import os

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

    # --- AGREGAR ESTA PARTE NUEVA ---
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    # --------------------------------

def run():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

def keep_alive():
    t = Thread(target=run)
    t.start()