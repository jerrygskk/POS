from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from lib.application_errors import (
    ConflictError, DatabaseError, InternalError, NotFoundError, ValidationError,
)
from lib.stock_service import StockFacade

router = APIRouter(prefix="/api")


class ReceiveIn(BaseModel):
    variant_id: int = Field(strict=True)
    qty: int = Field(gt=0, strict=True)
    note: str | None = None


def _call(request, action, payload):
    try:
        return StockFacade(request.app.state.db_path).invoke(action, payload)
    except (ValidationError, NotFoundError, ConflictError, DatabaseError, InternalError) as exc:
        status = {ValidationError: 422, NotFoundError: 404, ConflictError: 409}.get(type(exc), 500)
        message = exc.message if status < 500 else type(exc).default_message
        raise HTTPException(status, message) from exc


@router.post("/stock/receive")
def receive(body: ReceiveIn, request: Request):
    return _call(request, "stock.receive", body.model_dump())


@router.get("/stock/{variant_id}")
def detail(variant_id: int, request: Request):
    return _call(request, "stock.detail", {"variant_id": variant_id})
