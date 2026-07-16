from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from lib.application_errors import ApplicationError
from lib.stocktake_service import StocktakeFacade

router=APIRouter(prefix="/api")
class SessionIn(BaseModel):
    operator: str|None=None
    note: str|None=None
class ScanIn(BaseModel):
    variant_id:int=Field(strict=True)
    qty:int=Field(default=1,gt=0,strict=True)
class SetIn(BaseModel): counted_qty:int=Field(ge=0,strict=True)

def _call(request,action,payload):
    try: return StocktakeFacade(request.app.state.db_path).invoke(action,payload)
    except ApplicationError as exc:
        status={"validation_error":422,"not_found":404,"conflict":409}.get(exc.code,500)
        raise HTTPException(status,exc.message if status<500 else type(exc).default_message) from exc
@router.post("/stocktake")
def open_session(body:SessionIn,request:Request): return _call(request,"stocktake.create",body.model_dump())
@router.get("/stocktake")
def list_sessions(request:Request): return _call(request,"stocktake.list",{})
@router.post("/stocktake/{sid}/scan")
def scan(sid:int,body:ScanIn,request:Request): return _call(request,"stocktake.scan",{"session_id":sid,**body.model_dump()})
@router.put("/stocktake/{sid}/items/{variant_id}")
def set_counted(sid:int,variant_id:int,body:SetIn,request:Request): return _call(request,"stocktake.set_counted",{"session_id":sid,"variant_id":variant_id,**body.model_dump()})
@router.get("/stocktake/{sid}")
def detail(sid:int,request:Request): return _call(request,"stocktake.detail",{"session_id":sid})
@router.post("/stocktake/{sid}/close")
def close(sid:int,request:Request): return _call(request,"stocktake.close",{"session_id":sid})
