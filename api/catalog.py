from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.error_mapping import application_error_response
from lib.application_errors import ApplicationError
from lib.settings_service import SettingsFacade

router = APIRouter(prefix="/api")

class SimpleNew(BaseModel): name: str; sort: int | None = None
class SimplePatch(BaseModel): name: str | None = None; sort: int | None = None; active: int | None = None
class CategoryNew(SimpleNew): model_mode: str | None = None
class CategoryPatch(SimplePatch): model_mode: str | None = None
class BrandNew(SimpleNew): pass
class BrandPatch(SimplePatch): pass
class PhoneBrandNew(SimpleNew): pass
class PhoneBrandPatch(SimplePatch): pass
class ModelNew(BaseModel):
    phone_brand_id: int; name: str; alias: str | None = None; series: str | None = None; sort: int | None = None
class ModelPatch(BaseModel):
    phone_brand_id: int | None = None; name: str | None = None; alias: str | None = None; series: str | None = None; sort: int | None = None; active: int | None = None
class SortIds(BaseModel): ids: list[int]
class IdList(BaseModel): category_ids: list[int] = []
class FieldIdList(BaseModel): field_ids: list[int] = []
class CategoryFieldPatch(BaseModel):
    sort: int | None = None; required: int | None = None
    default_option_id: int | None = None; active: int | None = None

def _call(request, action, payload=None):
    try: return SettingsFacade(request.app.state.db_path).invoke(action, payload or {})
    except ApplicationError as exc:
        status, message = application_error_response(exc)
        raise HTTPException(status, message) from exc

def _register_simple(path, action, new_model, patch_model):
    async def list_items(request: Request, all: int = 0): return _call(request, action+".list", {"all":all})
    async def add_item(body: new_model, request: Request): return _call(request, action+".create", body.model_dump())
    async def patch_item(item_id: int, body: patch_model, request: Request): return _call(request, action+".update", {"id":item_id,"fields":body.model_dump(exclude_unset=True)})
    async def delete_item(item_id: int, request: Request): return _call(request, action+".delete", {"id":item_id})
    router.add_api_route("/"+path, list_items, methods=["GET"])
    router.add_api_route("/"+path, add_item, methods=["POST"])
    router.add_api_route("/"+path+"/{item_id}", patch_item, methods=["PATCH"])
    router.add_api_route("/"+path+"/{item_id}", delete_item, methods=["DELETE"])

_register_simple("categories","categories",CategoryNew,CategoryPatch)
_register_simple("phone-brands","phone_brands",PhoneBrandNew,PhoneBrandPatch)

@router.get("/brands")
def list_brands(request:Request,all:int=0,category_id:int|None=None): return _call(request,"brands.list",{"all":all,"category_id":category_id})
@router.post("/brands")
def add_brand(body:BrandNew,request:Request): return _call(request,"brands.create",body.model_dump())
@router.patch("/brands/{item_id}")
def patch_brand(item_id:int,body:BrandPatch,request:Request): return _call(request,"brands.update",{"id":item_id,"fields":body.model_dump(exclude_unset=True)})
@router.delete("/brands/{item_id}")
def delete_brand(item_id:int,request:Request): return _call(request,"brands.delete",{"id":item_id})

for _path,_action in (("categories","categories"),("brands","brands"),("phone-brands","phone_brands"),("models","models")):
    def sort_items(body:SortIds,request:Request,_action=_action): return _call(request,_action+".sort",{"ids":body.ids})
    router.add_api_route("/"+_path+"/sort",sort_items,methods=["PUT"])

@router.put("/brands/{bid}/categories")
def set_brand_categories(bid:int,body:IdList,request:Request): return _call(request,"brands.set_categories",{"id":bid,"category_ids":body.category_ids})
@router.get("/models")
def list_models(request:Request,all:int=0,phone_brand_id:int|None=None): return _call(request,"models.list",{"all":all,"phone_brand_id":phone_brand_id})
@router.post("/models")
def add_model(body:ModelNew,request:Request): return _call(request,"models.create",body.model_dump())
@router.patch("/models/{mid}")
def patch_model(mid:int,body:ModelPatch,request:Request): return _call(request,"models.update",{"id":mid,"fields":body.model_dump(exclude_unset=True)})
@router.delete("/models/{mid}")
def delete_model(mid:int,request:Request): return _call(request,"models.delete",{"id":mid})
@router.get("/categories/{cid}/fields")
def category_fields(cid:int,request:Request): return _call(request,"categories.fields",{"id":cid})
@router.put("/categories/{cid}/fields-common")
def common_fields(cid:int,body:FieldIdList,request:Request): return _call(request,"categories.set_common_fields",{"id":cid,"field_ids":body.field_ids})
@router.put("/categories/{cid}/fields/{fid}")
def set_category_field(cid:int,fid:int,body:CategoryFieldPatch,request:Request): return _call(request,"categories.set_field",{"category_id":cid,"field_id":fid,"fields":body.model_dump(exclude_unset=True)})
