import os, sys, threading, webbrowser
import uvicorn
from lib.db import init_db
from lib.backup import run_auto_backup
from api import create_app

PORT = int(os.environ.get("PORT", "8737"))

def data_dir():
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
        else os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, "data")
    os.makedirs(d, exist_ok=True)
    return d

def main():
    db_path = os.path.join(data_dir(), "pos.db")
    init_db(db_path)
    run_auto_backup(db_path)
    app = create_app(db_path)
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")

if __name__ == "__main__":
    main()
