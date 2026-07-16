from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from lib.application_errors import ApplicationError, ConflictError, DatabaseError, InternalError, NotFoundError, ValidationError
from lib.sales_service import SalesFacade, SalesRepository, SalesService
from lib.product_data import attrs_by_variant

router = APIRouter(prefix="/api")


class ItemIn(BaseModel):
    variant_id: int = Field(strict=True)
    qty: int = Field(gt=0, strict=True)
    unit_price: int = Field(ge=0, strict=True)
    discount: int = Field(default=0, ge=0, strict=True)


class SaleIn(BaseModel):
    payment: str
    order_discount: int = Field(default=0, ge=0, strict=True)
    paid: int = Field(ge=0, strict=True)
    items: list[ItemIn] = Field(min_length=1)


def _facade(request):
    return SalesFacade(request.app.state.db_path)


def _call(request, action, payload=None):
    try:
        return _facade(request).invoke(action, payload or {})
    except ApplicationError as exc:
        if isinstance(exc, ValidationError): status, message = 422, exc.message
        elif isinstance(exc, NotFoundError): status, message = 404, exc.message
        elif isinstance(exc, ConflictError): status, message = 409, exc.message
        elif isinstance(exc, DatabaseError): status, message = 500, DatabaseError.default_message
        else: status, message = 500, InternalError.default_message
        raise HTTPException(status, message) from exc


def _build_sale_filters(date_from, date_to, payment, sale_alias="s"):
    return SalesRepository.filter_sql({"date_from": date_from, "date_to": date_to, "payment": payment}, sale_alias)


def _load_sale_rows(conn, date_from, date_to, payment, load_attrs=True):
    service = SalesService(SalesRepository(conn))
    rows = service.repo.sale_rows({"date_from": date_from, "date_to": date_to, "payment": payment})
    from lib import product_data
    vids = [row["variant_id"] for row in rows]
    attrs = product_data.attrs_by_variant(conn, vids) if load_attrs else {}
    return rows, attrs, product_data.display_attrs(conn, vids)


@router.get("/payments")
def payments(request: Request):
    return _call(request, "payments.list")


@router.post("/sales")
def checkout(body: SaleIn, request: Request):
    return _call(request, "sales.checkout", body.model_dump())


@router.get("/sales")
def list_sales(request: Request, date_from: str = "", date_to: str = "", payment: str = ""):
    return _call(request, "sales.list", {"date_from": date_from, "date_to": date_to, "payment": payment})


@router.get("/sales/summary")
def summary(request: Request, date_from: str = "", date_to: str = "", payment: str = "", date: str = ""):
    return _call(request, "sales.summary", {"date_from": date_from, "date_to": date_to, "payment": payment, "date": date})


@router.get("/sales/export")
def export_csv(request: Request, date_from: str = "", date_to: str = "", payment: str = ""):
    exported = _call(request, "sales.export", {"date_from": date_from, "date_to": date_to, "payment": payment})
    return StreamingResponse(iter([exported["content"]]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{exported["filename"]}"'})
