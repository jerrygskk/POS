from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os, sys

def _static_dir():
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "static")
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")

def create_app(db_path):
    app = FastAPI(title="POS")
    app.state.db_path = db_path
    from api import attributes, catalog, products, stock, sales, stocktake, printing
    app.include_router(attributes.router)
    app.include_router(catalog.router)
    app.include_router(products.router)
    app.include_router(stock.router)
    app.include_router(sales.router)
    app.include_router(stocktake.router)
    app.include_router(printing.router)
    static_dir = _static_dir()
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app
