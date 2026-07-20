from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.error_mapping import application_error_response
from lib.application_errors import ApplicationError
from lib.product_service import ProductFacade

router = APIRouter(prefix="/api")


class BarcodeIn(BaseModel):
    barcode: str | None = None
    source: str = "store"


class VariantIn(BaseModel):
    attributes: dict = {}
    price: int | None = None
    model_ids: list[int] = []
    barcodes: list[BarcodeIn] = []


class ProductIn(BaseModel):
    name: str
    category_id: int
    brand_id: int | None = None
    brand_name: str | None = None
    note: str | None = None
    variants: list[VariantIn] = []


class ProductPatch(BaseModel):
    name: str | None = None
    category_id: int | None = None
    brand_id: int | None = None
    brand_name: str | None = None
    note: str | None = None
    active: int | None = None


class VariantPatch(BaseModel):
    attributes: dict | None = None
    price: int | None = None
    active: int | None = None


class NewVariantIn(VariantIn):
    pass


class ModelIdList(BaseModel):
    model_ids: list[int] = []


def _facade(request):
    return ProductFacade(request.app.state.db_path)


def _call(request, action, payload=None):
    try:
        return _facade(request).invoke(action, payload or {})
    except ApplicationError as exc:
        status, message = application_error_response(exc)
        raise HTTPException(status, message) from exc


@router.post("/products")
def create_product(body: ProductIn, request: Request):
    return _call(request, "products.create", body.model_dump())


@router.get("/barcode/{code}")
def scan(code: str, request: Request):
    return _call(request, "barcodes.scan", {"code": code})


@router.post("/variants/{variant_id}/barcodes")
def add_barcode(variant_id: int, body: BarcodeIn, request: Request):
    return _call(request, "barcodes.add", {"variant_id": variant_id, **body.model_dump()})


@router.get("/products")
def search(request: Request, q: str = "", category_id: int | None = None,
           brand_id: int | None = None, model_id: int | None = None):
    return _call(request, "products.list", {"q": q, "category_id": category_id,
                 "brand_id": brand_id, "model_id": model_id})


@router.get("/catalog")
def catalog(request: Request, q: str = "", include_inactive: bool = False,
            category_id: int | None = None, brand_id: int | None = None,
            model_id: int | None = None):
    return _call(request, "catalog.list", {"q": q, "include_inactive": include_inactive,
                 "category_id": category_id, "brand_id": brand_id, "model_id": model_id})


@router.put("/products/{pid}")
def update_product(pid: int, body: ProductPatch, request: Request):
    return _call(request, "products.update", {"id": pid, "fields": body.model_dump(exclude_unset=True)})


@router.put("/variants/{vid}")
def update_variant(vid: int, body: VariantPatch, request: Request):
    return _call(request, "variants.update", {"id": vid, "fields": body.model_dump(exclude_unset=True)})


@router.put("/variants/{vid}/models")
def set_variant_models(vid: int, body: ModelIdList, request: Request):
    return _call(request, "variants.set_models", {"id": vid, "model_ids": body.model_ids})


@router.post("/products/{pid}/variants")
def add_variant(pid: int, body: NewVariantIn, request: Request):
    return _call(request, "variants.create", {"product_id": pid, "fields": body.model_dump()})


@router.delete("/barcodes/{code}")
def delete_barcode(code: str, request: Request):
    return _call(request, "barcodes.delete", {"code": code})


@router.delete("/variants/{vid}")
def delete_variant(vid: int, request: Request):
    return _call(request, "variants.delete", {"id": vid})


@router.delete("/products/{pid}")
def delete_product(pid: int, request: Request):
    return _call(request, "products.delete", {"id": pid})
