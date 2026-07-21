"""
Optional Flask server — only needed if you deploy on Render as a Web Service
(the same pattern as SimplyAgenticAI) instead of a Static Site.

For a plain landing page the Static Site route (see render.yaml) is simpler,
faster, and free. Keep this file if you plan to add dynamic routes later
(contact form handler, API endpoints, etc.).

Run locally:   python app.py
Render start:  gunicorn app:app
"""
import os
from flask import Flask, send_from_directory

app = Flask(__name__, static_folder=".", static_url_path="")


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/<path:path>")
def assets(path):
    return send_from_directory(".", path)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
