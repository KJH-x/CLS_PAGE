#!/usr/bin/env python3
"""Local test server — serves site/ on http://localhost:17099"""
import http.server
import socketserver
import sys
import os

PORT = 17099
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "site")

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)
    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {args[0]}")

if __name__ == "__main__":
    print(f"Serving {DIR}")
    print(f"Open http://localhost:{PORT}")
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")
            httpd.shutdown()
