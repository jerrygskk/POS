from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from lib.application_errors import ApplicationError, ConflictError, NotFoundError
from lib.settings_service import SettingsFacade

router=APIRouter(prefix="/api")
class FieldPatch(BaseModel): name:str|None=None; sort:int|None=None; active:int|None=None; field_type:str|None=None; default_option_id:int|None=None
class FieldNew(BaseModel): name:str; category_id:int|None=None; field_type:str="select"; default_option_id:int|None=None
class OptionNew(BaseModel): field_id:int; value:str; reactivate:bool=False
class OptionPatch(BaseModel): value:str|None=None; sort:int|None=None; active:int|None=None
class OptionModelList(BaseModel): model_ids:list[int]=[]
def _call(request,action,payload=None):
    try:return SettingsFacade(request.app.state.db_path).invoke(action,payload or {})
    except ApplicationError as exc:
        status=404 if isinstance(exc,NotFoundError) else 409 if isinstance(exc,ConflictError) else 422
        raise HTTPException(status,exc.message) from exc
@router.get("/fields")
def fields(request:Request,category_id:int|None=None,common:int=0):return _call(request,"fields.list",{"category_id":category_id,"common":common})
@router.post("/fields")
def add_field(body:FieldNew,request:Request):return _call(request,"fields.create",body.model_dump())
@router.put("/fields/{fid}")
def patch_field(fid:int,body:FieldPatch,request:Request):return _call(request,"fields.update",{"id":fid,"fields":body.model_dump(exclude_unset=True)})
@router.get("/options")
def options(field_id:int,request:Request,all:int=0,model_ids:list[int]=Query(default=[])):return _call(request,"options.list",{"field_id":field_id,"all":all,"model_ids":model_ids})
@router.post("/options")
def add_option(body:OptionNew,request:Request):return _call(request,"options.create",body.model_dump())
@router.patch("/options/{oid}")
def patch_option(oid:int,body:OptionPatch,request:Request):return _call(request,"options.update",{"id":oid,"fields":body.model_dump(exclude_unset=True)})
@router.delete("/options/{oid}")
def delete_option(oid:int,request:Request):return _call(request,"options.delete",{"id":oid})
@router.get("/options/{oid}/models")
def option_models(oid:int,request:Request):return _call(request,"options.models",{"id":oid})
@router.put("/options/{oid}/models")
def set_option_models(oid:int,body:OptionModelList,request:Request):return _call(request,"options.set_models",{"id":oid,"model_ids":body.model_ids})
