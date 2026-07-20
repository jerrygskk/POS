from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.error_mapping import application_error_response
from lib.application_errors import ApplicationError
from lib.sales_service import SalesFacade

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
        status, message = application_error_response(exc)
        raise HTTPException(status, message) from exc


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
