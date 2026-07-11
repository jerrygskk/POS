from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api")

class PrintIn(BaseModel):
    barcode: str
    name: str = ""

@router.post("/print/barcode")
def print_barcode(body: PrintIn):
    # 預留介面:之後接實體條碼機時只改這裡
    raise HTTPException(501, "條碼列印尚未接上實體印表機")
