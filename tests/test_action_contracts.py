"""Desktop action payload contracts and the temporary HTTP parity harness.

The table below is deliberately executable.  A field is present only when the
corresponding Facade validator accepts it; actions without a Pydantic body model
say ``source='none'`` explicitly.  Phase 5 removes only the HTTP comparison
class, not the Facade contract table/tests.
"""
import copy
import importlib
import inspect
import re

from lib.application_errors import ConflictError, NotFoundError, ValidationError
from lib.desktop_application import DesktopFacade
from lib.desktop_bridge import DesktopBridge
from lib.db import get_conn
from tests.base import ApiTestCase, FacadeTestCase


NA = "not_applicable"


def field(path, value_type, *, required=False, nullable=False, default="missing",
          constraint="none", bool_policy="n/a", normalization="none",
          source="none", target=None, blank=NA,
          wrong=None, null=None, list_element=NA):
    """Materialise every §8.5 column and every applicable mutation expectation."""
    if wrong is None:
        wrong = [] if not value_type.startswith("list[") else "not-a-list"
    if null is None:
        null = "accept" if nullable else "reject"
    return {
        "payload_path": path, "required": required, "type": value_type,
        "nullable": nullable, "default": default, "range_enum": constraint,
        "bool_policy": bool_policy, "normalization": normalization,
        "source": source, "target": target,
        "cases": {
            "missing": "reject" if required else "accept",
            "wrong_type": {"expect": "reject", "value": wrong},
            "bool_int": ("reject" if bool_policy == "reject_as_int" else NA),
            "null": null, "blank": blank, "list_element": list_element,
        },
    }


def schema(fields, *, http="none", valid_error=None, notes=()):
    return {"fields": {item["payload_path"]: item for item in fields},
            "extra_fields": "reject", "http": http,
            "valid_error": valid_error, "notes": tuple(notes)}


I = lambda path, **kw: field(path, "int", bool_policy="reject_as_int", wrong="x", **kw)
S = lambda path, **kw: field(path, "str", wrong=1, **kw)
B = lambda path, **kw: field(path, "bool", wrong="true", **kw)
BI = lambda path, **kw: field(path, "bool|int", wrong="x", bool_policy="accept_bool", **kw)
LI = lambda path, **kw: field(path, "list[int]", wrong="x",
                              list_element={"expect": "reject", "value": "x"}, **kw)
M = lambda path, **kw: field(path, "mapping", wrong=[], **kw)

# Every key is written explicitly.  There is intentionally no action-name fallback.
ACTION_CONTRACTS = {
    "categories.list": schema([BI("all", default=0, source="none"), I("category_id", nullable=True, default=None, source="none")], http="GET /api/categories", notes=("Facade accepts but ignores category_id; HTTP has no such query parameter.",)),
    "categories.create": schema([S("name", required=True, source="api.catalog.CategoryNew", blank="accept"), I("sort", nullable=True, default=None, source="api.catalog.CategoryNew"), S("model_mode", nullable=True, default=None, constraint="required|hidden", source="api.catalog.CategoryNew")], http="POST /api/categories"),
    "categories.update": schema([I("id", required=True), M("fields", required=True, source="api.catalog.CategoryPatch"), S("fields.name", source="api.catalog.CategoryPatch", blank="accept"), I("fields.sort", source="api.catalog.CategoryPatch"), BI("fields.active", source="api.catalog.CategoryPatch"), S("fields.model_mode", constraint="required|hidden", source="api.catalog.CategoryPatch")], http="PATCH /api/categories/{id}"),
    "categories.delete": schema([I("id", required=True)], http="DELETE /api/categories/{id}"),
    "categories.sort": schema([LI("ids", required=True, source="api.catalog.SortIds")], http="PUT /api/categories/sort"),
    "categories.fields": schema([I("id", required=True)], http="GET /api/categories/{id}/fields"),
    "categories.set_common_fields": schema([I("id", required=True), LI("field_ids", required=True, source="api.catalog.FieldIdList")], http="PUT /api/categories/{id}/fields-common"),
    "categories.set_field": schema([I("category_id", required=True), I("field_id", required=True), M("fields", required=True, source="api.catalog.CategoryFieldPatch"), I("fields.sort", source="api.catalog.CategoryFieldPatch"), BI("fields.required", source="api.catalog.CategoryFieldPatch"), I("fields.default_option_id", nullable=True, default=None, source="api.catalog.CategoryFieldPatch"), BI("fields.active", source="api.catalog.CategoryFieldPatch")], http="PUT /api/categories/{id}/fields/{field_id}"),

    "brands.list": schema([BI("all", default=0), I("category_id", nullable=True, default=None)], http="GET /api/brands"),
    "brands.create": schema([S("name", required=True, source="api.catalog.BrandNew", blank="accept"), I("sort", nullable=True, default=None, source="api.catalog.BrandNew")], http="POST /api/brands"),
    "brands.update": schema([I("id", required=True), M("fields", required=True, source="api.catalog.BrandPatch"), S("fields.name", source="api.catalog.BrandPatch", blank="accept"), I("fields.sort", source="api.catalog.BrandPatch"), BI("fields.active", source="api.catalog.BrandPatch")], http="PATCH /api/brands/{id}"),
    "brands.delete": schema([I("id", required=True)], http="DELETE /api/brands/{id}"),
    "brands.sort": schema([LI("ids", required=True, source="api.catalog.SortIds")], http="PUT /api/brands/sort"),
    "brands.set_categories": schema([I("id", required=True), LI("category_ids", required=True, source="api.catalog.IdList")], http="PUT /api/brands/{id}/categories"),

    "phone_brands.list": schema([BI("all", default=0), I("category_id", nullable=True, default=None)], http="GET /api/phone-brands"),
    "phone_brands.create": schema([S("name", required=True, source="api.catalog.PhoneBrandNew", blank="accept"), I("sort", nullable=True, default=None, source="api.catalog.PhoneBrandNew")], http="POST /api/phone-brands"),
    "phone_brands.update": schema([I("id", required=True), M("fields", required=True, source="api.catalog.PhoneBrandPatch"), S("fields.name", source="api.catalog.PhoneBrandPatch", blank="accept"), I("fields.sort", source="api.catalog.PhoneBrandPatch"), BI("fields.active", source="api.catalog.PhoneBrandPatch")], http="PATCH /api/phone-brands/{id}"),
    "phone_brands.delete": schema([I("id", required=True)], http="DELETE /api/phone-brands/{id}"),
    "phone_brands.sort": schema([LI("ids", required=True, source="api.catalog.SortIds")], http="PUT /api/phone-brands/sort"),

    "models.list": schema([BI("all", default=0), I("phone_brand_id", nullable=True, default=None)], http="GET /api/models"),
    "models.create": schema([I("phone_brand_id", required=True, source="api.catalog.ModelNew"), S("name", required=True, source="api.catalog.ModelNew", blank="accept"), S("alias", nullable=True, default=None, source="api.catalog.ModelNew"), S("series", nullable=True, default=None, source="api.catalog.ModelNew", normalization="trim; blank becomes None"), I("sort", nullable=True, default=None, source="api.catalog.ModelNew")], http="POST /api/models"),
    "models.update": schema([I("id", required=True), M("fields", required=True, source="api.catalog.ModelPatch"), I("fields.phone_brand_id", source="api.catalog.ModelPatch"), S("fields.name", source="api.catalog.ModelPatch", blank="accept"), S("fields.alias", nullable=True, default=None, source="api.catalog.ModelPatch"), S("fields.series", nullable=True, default=None, source="api.catalog.ModelPatch", normalization="trim; blank becomes None"), I("fields.sort", source="api.catalog.ModelPatch"), BI("fields.active", source="api.catalog.ModelPatch")], http="PATCH /api/models/{id}"),
    "models.delete": schema([I("id", required=True)], http="DELETE /api/models/{id}"),
    "models.sort": schema([LI("ids", required=True, source="api.catalog.SortIds")], http="PUT /api/models/sort"),

    "fields.list": schema([I("category_id", nullable=True, default=None), BI("common", default=0)], http="GET /api/fields"),
    "fields.create": schema([S("name", required=True, source="api.attributes.FieldNew", blank="accept"), I("category_id", nullable=True, default=None, source="api.attributes.FieldNew"), S("field_type", default="select", constraint="select|text|multi|tags", source="api.attributes.FieldNew"), I("default_option_id", nullable=True, default=None, constraint="must be None on create", source="api.attributes.FieldNew")], http="POST /api/fields"),
    "fields.update": schema([I("id", required=True), M("fields", required=True, source="api.attributes.FieldPatch"), S("fields.name", source="api.attributes.FieldPatch", blank="accept"), I("fields.sort", source="api.attributes.FieldPatch"), BI("fields.active", source="api.attributes.FieldPatch"), S("fields.field_type", constraint="select|text|multi|tags", source="api.attributes.FieldPatch"), I("fields.default_option_id", nullable=True, default=None, source="api.attributes.FieldPatch")], http="PUT /api/fields/{id}"),
    "options.list": schema([I("field_id", required=True), BI("all", default=0), LI("model_ids", default=[])], http="GET /api/options"),
    "options.create": schema([I("field_id", required=True, source="api.attributes.OptionNew"), S("value", required=True, source="api.attributes.OptionNew", blank="accept"), B("reactivate", default=False, source="api.attributes.OptionNew")], http="POST /api/options"),
    "options.update": schema([I("id", required=True), M("fields", required=True, source="api.attributes.OptionPatch"), S("fields.value", source="api.attributes.OptionPatch", blank="accept"), I("fields.sort", source="api.attributes.OptionPatch"), BI("fields.active", source="api.attributes.OptionPatch")], http="PATCH /api/options/{id}"),
    "options.delete": schema([I("id", required=True)], http="DELETE /api/options/{id}"),
    "options.models": schema([I("id", required=True)], http="GET /api/options/{id}/models"),
    "options.set_models": schema([I("id", required=True), LI("model_ids", required=True, source="api.attributes.OptionModelList")], http="PUT /api/options/{id}/models"),
    "options.cleanup": schema([I("field_id", nullable=True, default=None)], notes=("No HTTP route/Pydantic model.",)),

    "products.create": schema([S("name", required=True, source="api.products.ProductIn", blank="accept", normalization="normalize_key only for duplicate comparison; stored unchanged"), I("category_id", required=True, source="api.products.ProductIn"), I("brand_id", nullable=True, default=None, source="api.products.ProductIn"), S("brand_name", nullable=True, default=None, source="api.products.ProductIn", blank="accept", normalization="normalize_key for reuse; normalize_display when creating"), S("note", nullable=True, default=None, source="api.products.ProductIn", blank="accept"), field("variants", "list[VariantIn]", default=[], source="api.products.ProductIn", wrong="x", list_element={"expect":"reject","value":"x"}), M("variants[].attributes", source="api.products.VariantIn", normalization="field names exact-match (feature key excepted); select/multi/tags values str.strip and deduplicate preserving order; text values stored unchanged"), I("variants[].price", nullable=True, default=None, source="api.products.VariantIn"), I("variants[].active", nullable=True, default=None, source="none"), LI("variants[].model_ids", default=[], source="api.products.VariantIn"), field("variants[].barcodes", "list[BarcodeIn]", default=[], source="api.products.VariantIn", wrong="x", list_element={"expect":"reject","value":"x"}), S("variants[].barcodes[].barcode", nullable=True, default=None, source="api.products.BarcodeIn", normalization="falsy generates next store barcode; stored nonempty string unchanged"), S("variants[].barcodes[].source", default="store", source="api.products.BarcodeIn")], http="POST /api/products", notes=("variants[].active is Desktop-only; ProductIn drops it.",)),
    "products.list": schema([S("q", default="", blank="accept"), I("category_id", nullable=True, default=None), I("brand_id", nullable=True, default=None), I("model_id", nullable=True, default=None)], http="GET /api/products"),
    "products.update": schema([I("id", required=True), M("fields", default={}, normalization="missing becomes empty mapping", source="api.products.ProductPatch"), S("fields.name", nullable=True, source="api.products.ProductPatch", blank="accept"), I("fields.category_id", nullable=True, source="api.products.ProductPatch"), I("fields.brand_id", nullable=True, source="api.products.ProductPatch"), S("fields.brand_name", nullable=True, source="api.products.ProductPatch", blank="accept"), S("fields.note", nullable=True, source="api.products.ProductPatch", blank="accept"), I("fields.active", nullable=True, source="api.products.ProductPatch")], http="PUT /api/products/{id}"),
    "products.delete": schema([I("id", required=True)], http="DELETE /api/products/{id}"),
    "catalog.list": schema([S("q", default="", blank="accept"), B("include_inactive", default=False), I("category_id", nullable=True, default=None), I("brand_id", nullable=True, default=None), I("model_id", nullable=True, default=None), B("pending", default=False)], http="GET /api/catalog", notes=("pending has no HTTP query parameter.",)),
    "variants.create": schema([I("product_id", required=True), M("fields", required=True, source="api.products.NewVariantIn"), M("fields.attributes", default={}, source="api.products.NewVariantIn"), I("fields.price", nullable=True, default=None, source="api.products.NewVariantIn"), I("fields.active", nullable=True, default=None, source="none"), LI("fields.model_ids", default=[], source="api.products.NewVariantIn"), field("fields.barcodes", "list[BarcodeIn]", default=[], source="api.products.NewVariantIn", wrong="x", list_element={"expect":"reject","value":"x"}), S("fields.barcodes[].barcode", nullable=True, default=None, source="api.products.BarcodeIn"), S("fields.barcodes[].source", default="store", source="api.products.BarcodeIn")], http="POST /api/products/{id}/variants", notes=("fields.active is Desktop-only; NewVariantIn drops it.",)),
    "variants.update": schema([I("id", required=True), M("fields", default={}), M("fields.attributes", source="api.products.VariantPatch"), I("fields.price", nullable=True, source="api.products.VariantPatch"), I("fields.active", nullable=True, source="api.products.VariantPatch")], http="PUT /api/variants/{id}", notes=("Facade rejects attributes=null although Pydantic accepts it.",)),
    "variants.set_models": schema([I("id", required=True), LI("model_ids", default=[], source="api.products.ModelIdList")], http="PUT /api/variants/{id}/models"),
    "variants.update_details": schema([I("id", required=True), M("fields", default={}), M("fields.attributes"), I("fields.price", nullable=True), I("fields.active", nullable=True), LI("model_ids", default=[])], notes=("No HTTP route/Pydantic model.",)),
    "variants.delete": schema([I("id", required=True)], http="DELETE /api/variants/{id}"),
    "variants.batch_create": schema([I("product_id", required=True), field("drafts", "list[Draft]", required=True, constraint="min_length=1", wrong="x", list_element={"expect":"reject","value":"x"}), S("drafts[].draft_id", nullable=True), M("drafts[].attributes", normalization="field names normalize_key; display values normalize_display; duplicate values removed"), I("drafts[].price", nullable=True), I("drafts[].active", nullable=True, default=1, normalization="truthy becomes 1, falsy becomes 0"), LI("drafts[].model_ids", default=[], normalization="deduplicate preserving order"), field("drafts[].barcodes", "list[Barcode]", default=[], wrong="x", list_element={"expect":"reject","value":"x"}), S("drafts[].barcodes[].barcode", nullable=True, default=None, normalization="trim; blank becomes None"), S("drafts[].barcodes[].source", default="store", normalization="falsy becomes factory for supplied code, store for generated code")], notes=("No HTTP route/Pydantic model.",)),
    "variants.field_usage": schema([I("category_id", required=True), I("field_id", required=True)], notes=("No HTTP route/Pydantic model.",)),
    "variants.activate": schema([I("id", required=True)], notes=("No HTTP route/Pydantic model.",)),
    "variants.issues": schema([], notes=("No HTTP route/Pydantic model.",)),
    "barcodes.scan": schema([S("code", required=True, blank="accept")], http="GET /api/barcode/{code}"),
    "barcodes.add": schema([I("variant_id", required=True), S("barcode", nullable=True, default=None, source="api.products.BarcodeIn", blank="accept"), S("source", default="store", source="api.products.BarcodeIn", blank="accept")], http="POST /api/variants/{id}/barcodes"),
    "barcodes.delete": schema([S("code", required=True, blank="accept")], http="DELETE /api/barcodes/{code}"),

    "stock.receive": schema([I("variant_id", required=True, source="api.stock.ReceiveIn"), I("qty", required=True, constraint=">0", source="api.stock.ReceiveIn"), S("note", nullable=True, default=None, source="api.stock.ReceiveIn", blank="accept")], http="POST /api/stock/receive"),
    "stock.detail": schema([I("variant_id", required=True)], http="GET /api/stock/{variant_id}"),
    "stocktake.create": schema([S("operator", nullable=True, default=None, source="api.stocktake.SessionIn", blank="accept"), S("note", nullable=True, default=None, source="api.stocktake.SessionIn", blank="accept")], http="POST /api/stocktake"),
    "stocktake.list": schema([], http="GET /api/stocktake"),
    "stocktake.detail": schema([I("session_id", required=True)], http="GET /api/stocktake/{sid}"),
    "stocktake.scan": schema([I("session_id", required=True), I("variant_id", required=True, source="api.stocktake.ScanIn"), I("qty", required=True, constraint=">0", source="api.stocktake.ScanIn")], http="POST /api/stocktake/{sid}/scan", notes=("Pydantic defaults qty=1; strict Desktop contract requires it.",)),
    "stocktake.set_counted": schema([I("session_id", required=True), I("variant_id", required=True), I("counted_qty", required=True, constraint=">=0", source="api.stocktake.SetIn")], http="PUT /api/stocktake/{sid}/items/{variant_id}"),
    "stocktake.close": schema([I("session_id", required=True)], http="POST /api/stocktake/{sid}/close"),
    "payments.list": schema([], http="GET /api/payments"),
    "sales.checkout": schema([S("payment", required=True, constraint="must be present in Setting.payments", source="api.sales.SaleIn", blank="reject"), I("order_discount", default=0, constraint=">=0", source="api.sales.SaleIn"), I("paid", required=True, constraint=">=0", source="api.sales.SaleIn"), field("items", "list[ItemIn]", required=True, constraint="min_length=1", source="api.sales.SaleIn", wrong="x", list_element={"expect":"reject","value":"x"}), I("items[].variant_id", required=True, source="api.sales.ItemIn"), I("items[].qty", required=True, constraint=">0", source="api.sales.ItemIn"), I("items[].unit_price", required=True, constraint=">=0", source="api.sales.ItemIn"), I("items[].discount", default=0, constraint="0..qty*unit_price", source="api.sales.ItemIn")], http="POST /api/sales"),
    "sales.list": schema([S("date_from", default="", constraint="empty|YYYY-MM-DD", blank="reject"), S("date_to", default="", constraint="empty|YYYY-MM-DD", blank="reject"), S("payment", default="", blank="accept")], http="GET /api/sales"),
    "sales.summary": schema([S("date_from", default="", constraint="empty|YYYY-MM-DD", blank="reject"), S("date_to", default="", constraint="empty|YYYY-MM-DD", blank="reject"), S("payment", default="", blank="accept"), S("date", default="", constraint="empty|YYYY-MM-DD", blank="reject", normalization="copied to date_from/date_to when both absent")], http="GET /api/sales/summary"),
    "sales.export_save": schema([S("date_from", default="", constraint="empty|YYYY-MM-DD", blank="reject"), S("date_to", default="", constraint="empty|YYYY-MM-DD", blank="reject"), S("payment", default="", blank="accept")], notes=("Transport-only; forwards unchanged to sales.export.",)),
    "printing.barcode": schema([I("variant_id", required=True, source="none")], http="different legacy POST /api/print/barcode contract", valid_error="validation_error", notes=("Desktop requires variant_id; legacy HTTP accepts barcode/name and always returns 501.",)),
}

INTERNAL_ACTION_CONTRACTS = {
    "sales.export": schema([S("date_from", default="", constraint="empty|YYYY-MM-DD", blank="reject"), S("date_to", default="", constraint="empty|YYYY-MM-DD", blank="reject"), S("payment", default="", blank="accept")], http="GET /api/sales/export", notes=("Internal target of sales.export_save.",))
}

# Exact FastAPI parameter/Pydantic ownership for every HTTP-backed Facade field.
# A contract field absent from this registry is deliberately Facade-only and must
# say ``source="none"``.  Values name real endpoint parameters or body models.
HTTP_FIELD_SOURCES = {
    "categories.list": {"all":"api.catalog.list_items.all"},
    "categories.create": {p:"api.catalog.CategoryNew" for p in ("name","sort","model_mode")},
    "categories.update": {"id":"api.catalog.patch_item.item_id", **{p:"api.catalog.CategoryPatch" for p in ("fields","fields.name","fields.sort","fields.active","fields.model_mode")}},
    "categories.delete": {"id":"api.catalog.delete_item.item_id"},
    "categories.sort": {"ids":"api.catalog.SortIds"},
    "categories.fields": {"id":"api.catalog.category_fields.cid"},
    "categories.set_common_fields": {"id":"api.catalog.common_fields.cid", "field_ids":"api.catalog.FieldIdList"},
    "categories.set_field": {"category_id":"api.catalog.set_category_field.cid", "field_id":"api.catalog.set_category_field.fid", **{p:"api.catalog.CategoryFieldPatch" for p in ("fields","fields.sort","fields.required","fields.default_option_id","fields.active")}},
    "brands.list": {"all":"api.catalog.list_brands.all", "category_id":"api.catalog.list_brands.category_id"},
    "brands.create": {p:"api.catalog.BrandNew" for p in ("name","sort")},
    "brands.update": {"id":"api.catalog.patch_brand.item_id", **{p:"api.catalog.BrandPatch" for p in ("fields","fields.name","fields.sort","fields.active")}},
    "brands.delete": {"id":"api.catalog.delete_brand.item_id"},
    "brands.sort": {"ids":"api.catalog.SortIds"},
    "brands.set_categories": {"id":"api.catalog.set_brand_categories.bid", "category_ids":"api.catalog.IdList"},
    "phone_brands.list": {"all":"api.catalog.list_items.all"},
    "phone_brands.create": {p:"api.catalog.PhoneBrandNew" for p in ("name","sort")},
    "phone_brands.update": {"id":"api.catalog.patch_item.item_id", **{p:"api.catalog.PhoneBrandPatch" for p in ("fields","fields.name","fields.sort","fields.active")}},
    "phone_brands.delete": {"id":"api.catalog.delete_item.item_id"},
    "phone_brands.sort": {"ids":"api.catalog.SortIds"},
    "models.list": {"all":"api.catalog.list_models.all", "phone_brand_id":"api.catalog.list_models.phone_brand_id"},
    "models.create": {p:"api.catalog.ModelNew" for p in ("phone_brand_id","name","alias","series","sort")},
    "models.update": {"id":"api.catalog.patch_model.mid", **{p:"api.catalog.ModelPatch" for p in ("fields","fields.phone_brand_id","fields.name","fields.alias","fields.series","fields.sort","fields.active")}},
    "models.delete": {"id":"api.catalog.delete_model.mid"},
    "models.sort": {"ids":"api.catalog.SortIds"},
    "fields.list": {p:f"api.attributes.fields.{p}" for p in ("category_id","common")},
    "fields.create": {p:"api.attributes.FieldNew" for p in ("name","category_id","field_type","default_option_id")},
    "fields.update": {"id":"api.attributes.patch_field.fid", **{p:"api.attributes.FieldPatch" for p in ("fields","fields.name","fields.sort","fields.active","fields.field_type","fields.default_option_id")}},
    "options.list": {p:f"api.attributes.options.{p}" for p in ("field_id","all","model_ids")},
    "options.create": {p:"api.attributes.OptionNew" for p in ("field_id","value","reactivate")},
    "options.update": {"id":"api.attributes.patch_option.oid", **{p:"api.attributes.OptionPatch" for p in ("fields","fields.value","fields.sort","fields.active")}},
    "options.delete": {"id":"api.attributes.delete_option.oid"},
    "options.models": {"id":"api.attributes.option_models.oid"},
    "options.set_models": {"id":"api.attributes.set_option_models.oid", "model_ids":"api.attributes.OptionModelList"},
    "products.create": {**{p:"api.products.ProductIn" for p in ("name","category_id","brand_id","brand_name","note","variants")}, **{p:"api.products.VariantIn" for p in ("variants[].attributes","variants[].price","variants[].model_ids","variants[].barcodes")}, **{p:"api.products.BarcodeIn" for p in ("variants[].barcodes[].barcode","variants[].barcodes[].source")}},
    "products.list": {p:f"api.products.search.{p}" for p in ("q","category_id","brand_id","model_id")},
    "products.update": {"id":"api.products.update_product.pid", **{p:"api.products.ProductPatch" for p in ("fields","fields.name","fields.category_id","fields.brand_id","fields.brand_name","fields.note","fields.active")}},
    "products.delete": {"id":"api.products.delete_product.pid"},
    "catalog.list": {p:f"api.products.catalog.{p}" for p in ("q","include_inactive","category_id","brand_id","model_id")},
    "variants.create": {"product_id":"api.products.add_variant.pid", **{p:"api.products.NewVariantIn" for p in ("fields","fields.attributes","fields.price","fields.model_ids","fields.barcodes")}, **{p:"api.products.BarcodeIn" for p in ("fields.barcodes[].barcode","fields.barcodes[].source")}},
    "variants.update": {"id":"api.products.update_variant.vid", **{p:"api.products.VariantPatch" for p in ("fields","fields.attributes","fields.price","fields.active")}},
    "variants.set_models": {"id":"api.products.set_variant_models.vid", "model_ids":"api.products.ModelIdList"},
    "variants.delete": {"id":"api.products.delete_variant.vid"},
    "barcodes.scan": {"code":"api.products.scan.code"},
    "barcodes.add": {"variant_id":"api.products.add_barcode.variant_id", **{p:"api.products.BarcodeIn" for p in ("barcode","source")}},
    "barcodes.delete": {"code":"api.products.delete_barcode.code"},
    "stock.receive": {p:"api.stock.ReceiveIn" for p in ("variant_id","qty","note")},
    "stock.detail": {"variant_id":"api.stock.detail.variant_id"},
    "stocktake.create": {p:"api.stocktake.SessionIn" for p in ("operator","note")},
    "stocktake.list": {},
    "stocktake.scan": {"session_id":"api.stocktake.scan.sid", **{p:"api.stocktake.ScanIn" for p in ("variant_id","qty")}},
    "stocktake.set_counted": {"session_id":"api.stocktake.set_counted.sid", "variant_id":"api.stocktake.set_counted.variant_id", "counted_qty":"api.stocktake.SetIn"},
    "stocktake.detail": {"session_id":"api.stocktake.detail.sid"},
    "stocktake.close": {"session_id":"api.stocktake.close.sid"},
    "payments.list": {},
    "sales.checkout": {**{p:"api.sales.SaleIn" for p in ("payment","order_discount","paid","items")}, **{p:"api.sales.ItemIn" for p in ("items[].variant_id","items[].qty","items[].unit_price","items[].discount")}},
    "sales.list": {p:f"api.sales.list_sales.{p}" for p in ("date_from","date_to","payment")},
    "sales.summary": {p:f"api.sales.summary.{p}" for p in ("date_from","date_to","payment","date")},
    "sales.export": {p:f"api.sales.export_csv.{p}" for p in ("date_from","date_to","payment")},
}

PYDANTIC_BODY_WRAPPER_FIELDS = {
    ("categories.update","fields"), ("categories.set_field","fields"),
    ("brands.update","fields"), ("phone_brands.update","fields"),
    ("models.update","fields"), ("fields.update","fields"),
    ("options.update","fields"), ("products.update","fields"),
    ("variants.create","fields"), ("variants.update","fields"),
}

# Source is contract data, not an optional annotation: materialise every field
# from the audited registry so Facade-only fields are explicitly ``none``.
for _action, _contract in {**ACTION_CONTRACTS, **INTERNAL_ACTION_CONTRACTS}.items():
    for _path, _spec in _contract["fields"].items():
        _spec["source"] = HTTP_FIELD_SOURCES.get(_action, {}).get(_path, "none")


def validate_source_registry_symbols():
    """Reject registry labels that do not resolve to a real model or parameter."""
    for source in {value for fields in HTTP_FIELD_SOURCES.values()
                   for value in fields.values()}:
        parts = source.split(".")
        module_name, owner_name, parameter = ".".join(parts[:2]), parts[2], parts[3:]
        module = importlib.import_module(module_name)
        owner = getattr(module, owner_name, None)
        if owner is None:
            owner = next((route.endpoint for route in module.router.routes
                          if route.endpoint.__name__ == owner_name), None)
        if owner is None:
            raise AssertionError(f"unknown source owner {module_name}.{owner_name}")
        if parameter:
            if parameter[0] not in inspect.signature(owner).parameters:
                raise AssertionError(f"unknown source parameter {source}")
        elif not hasattr(owner, "model_fields"):
            raise AssertionError(f"source is not a Pydantic model: {source}")
    for action, fields in HTTP_FIELD_SOURCES.items():
        for path, source in fields.items():
            parts = source.split(".")
            if len(parts) != 3:
                continue
            model = getattr(importlib.import_module(".".join(parts[:2])), parts[2])
            leaf = path.rsplit(".", 1)[-1].removesuffix("[]")
            if (action, path) not in PYDANTIC_BODY_WRAPPER_FIELDS and \
                    leaf not in model.model_fields:
                raise AssertionError(f"{source} has no field for {action}.{path}")


def validate_source_metadata(contracts):
    validate_source_registry_symbols()
    declared_http_actions = {
        action for action, contract in contracts.items()
        if re.match(r"^(GET|POST|PUT|PATCH|DELETE) /api/", contract["http"])
    }
    if set(HTTP_FIELD_SOURCES) != declared_http_actions:
        raise AssertionError(
            "HTTP source action registry mismatch: "
            f"expected {sorted(declared_http_actions)}, got {sorted(HTTP_FIELD_SOURCES)}")
    expected = {(action, path): source for action, fields in HTTP_FIELD_SOURCES.items()
                for path, source in fields.items()}
    known_sources = set(expected.values())
    actual_pairs = {(action, path) for action, contract in contracts.items()
                    for path in contract["fields"]}
    unknown_registry_pairs = set(expected) - actual_pairs
    if unknown_registry_pairs:
        raise AssertionError(f"source registry refers to unknown fields: {sorted(unknown_registry_pairs)}")
    for action, contract in contracts.items():
        for path, spec in contract["fields"].items():
            wanted = expected.get((action, path), "none")
            source = spec["source"]
            if source != "none" and source not in known_sources:
                raise AssertionError(f"unknown source {source!r} for {action}.{path}")
            if source != wanted:
                raise AssertionError(
                    f"wrong source for {action}.{path}: expected {wanted!r}, got {source!r}")

SETTINGS_CONTRACT_ACTIONS = {
    "categories.list","categories.create","categories.update","categories.delete","categories.sort","categories.fields","categories.set_common_fields","categories.set_field",
    "brands.list","brands.create","brands.update","brands.delete","brands.sort","brands.set_categories",
    "phone_brands.list","phone_brands.create","phone_brands.update","phone_brands.delete","phone_brands.sort",
    "models.list","models.create","models.update","models.delete","models.sort",
    "fields.list","fields.create","fields.update","options.list","options.create","options.update","options.delete","options.models","options.set_models","options.cleanup",
}
PRODUCT_CONTRACT_ACTIONS = {
    "products.create","products.list","products.update","products.delete","catalog.list",
    "variants.create","variants.update","variants.set_models","variants.update_details","variants.delete","variants.batch_create","variants.field_usage","variants.activate","variants.issues",
    "barcodes.scan","barcodes.add","barcodes.delete",
}
TARGET_BY_ACTION = {
    **{action:"lib.settings_service.SettingsFacade._prepare_payload -> _validate_action" for action in SETTINGS_CONTRACT_ACTIONS},
    **{action:"lib.product_service.ProductFacade._prepare_payload -> _validate_action_payload" for action in PRODUCT_CONTRACT_ACTIONS},
    "stock.receive":"lib.stock_service.StockFacade._prepare_payload -> _validate_payload",
    "stock.detail":"lib.stock_service.StockFacade._prepare_payload -> _validate_payload",
    "stocktake.create":"lib.stocktake_service.StocktakeFacade._prepare_payload -> _validate",
    "stocktake.list":"lib.stocktake_service.StocktakeFacade._prepare_payload -> _validate",
    "stocktake.detail":"lib.stocktake_service.StocktakeFacade._prepare_payload -> _validate",
    "stocktake.scan":"lib.stocktake_service.StocktakeFacade._prepare_payload -> _validate",
    "stocktake.set_counted":"lib.stocktake_service.StocktakeFacade._prepare_payload -> _validate",
    "stocktake.close":"lib.stocktake_service.StocktakeFacade._prepare_payload -> _validate",
    "payments.list":"lib.sales_service.SalesFacade._prepare_payload -> _strict_mapping",
    "sales.checkout":"lib.sales_service.SalesFacade._prepare_payload -> _checkout_payload",
    "sales.list":"lib.sales_service.SalesFacade._prepare_payload -> _filters",
    "sales.summary":"lib.sales_service.SalesFacade._prepare_payload -> _filters",
    "sales.export_save":"lib.desktop_bridge.DesktopBridge._export_sales -> lib.sales_service.SalesFacade._prepare_payload",
    "printing.barcode":"lib.printing_service.PrintingFacade.invoke",
    "sales.export":"lib.sales_service.SalesFacade._prepare_payload -> _filters",
}
for _action, _contract in {**ACTION_CONTRACTS, **INTERNAL_ACTION_CONTRACTS}.items():
    for _spec in _contract["fields"].values():
        _spec["target"] = TARGET_BY_ACTION[_action]

FRONTEND_ACTIONS = set(ACTION_CONTRACTS)

PYDANTIC_DIFFERENCES = (
    {"kind":"coercion", "scope":"non-strict catalogue/product bodies and FastAPI path/query annotations", "desktop":"reject wrong primitive type", "http":"may coerce before Facade"},
    {"kind":"extras", "scope":"Pydantic body models with default extra policy", "desktop":"reject", "http":"ignore before Facade"},
    {"kind":"nullable", "scope":"api.products.VariantPatch.attributes", "desktop":"reject None", "http":"Pydantic accepts, then Facade rejects"},
    {"kind":"default", "scope":"api.stocktake.ScanIn.qty", "desktop":"required", "http":"defaults to 1"},
    {"kind":"separate_contract", "scope":"printing.barcode", "desktop":"variant_id then explicit unsupported error", "http":"barcode/name then HTTP 501"},
)

NORMALIZATION_PROBE_PATHS = {
    ("models.create","series"), ("models.update","fields.series"),
    ("products.create","name"), ("products.create","brand_name"),
    ("products.create","variants[].attributes"),
    ("products.create","variants[].barcodes[].barcode"),
    ("products.update","fields"),
    ("variants.batch_create","drafts[].attributes"),
    ("variants.batch_create","drafts[].active"),
    ("variants.batch_create","drafts[].model_ids"),
    ("variants.batch_create","drafts[].barcodes[].barcode"),
    ("variants.batch_create","drafts[].barcodes[].source"),
    ("sales.summary","date"),
}

# Hand-audited literals: deliberately independent from ACTION_CONTRACTS so a
# mutated metadata default cannot also manufacture its own expected value.
EXPECTED_DEFAULTS = {
    **dict.fromkeys({
        ("categories.list","all"), ("brands.list","all"),
        ("phone_brands.list","all"), ("models.list","all"),
        ("fields.list","common"), ("options.list","all"),
        ("sales.checkout","order_discount"),
        ("sales.checkout","items[].discount"),
    }, 0),
    **dict.fromkeys({
        ("categories.list","category_id"), ("categories.create","sort"),
        ("categories.create","model_mode"),
        ("categories.set_field","fields.default_option_id"),
        ("brands.list","category_id"), ("brands.create","sort"),
        ("phone_brands.list","category_id"), ("phone_brands.create","sort"),
        ("models.list","phone_brand_id"), ("models.create","alias"),
        ("models.create","series"), ("models.create","sort"),
        ("models.update","fields.alias"), ("models.update","fields.series"),
        ("fields.list","category_id"), ("fields.create","category_id"),
        ("fields.create","default_option_id"),
        ("fields.update","fields.default_option_id"),
        ("options.cleanup","field_id"), ("products.create","brand_id"),
        ("products.create","brand_name"), ("products.create","note"),
        ("products.create","variants[].price"),
        ("products.create","variants[].active"),
        ("products.create","variants[].barcodes[].barcode"),
        ("products.list","category_id"), ("products.list","brand_id"),
        ("products.list","model_id"), ("catalog.list","category_id"),
        ("catalog.list","brand_id"), ("catalog.list","model_id"),
        ("variants.create","fields.price"),
        ("variants.create","fields.active"),
        ("variants.create","fields.barcodes[].barcode"),
        ("variants.batch_create","drafts[].barcodes[].barcode"),
        ("barcodes.add","barcode"), ("stock.receive","note"),
        ("stocktake.create","operator"), ("stocktake.create","note"),
    }, None),
    ("fields.create","field_type"): "select",
    **dict.fromkeys({
        ("options.list","model_ids"), ("products.create","variants"),
        ("products.create","variants[].model_ids"),
        ("products.create","variants[].barcodes"),
        ("variants.create","fields.model_ids"),
        ("variants.create","fields.barcodes"),
        ("variants.set_models","model_ids"),
        ("variants.update_details","model_ids"),
        ("variants.batch_create","drafts[].model_ids"),
        ("variants.batch_create","drafts[].barcodes"),
    }, []),
    **dict.fromkeys({
        ("options.create","reactivate"), ("catalog.list","include_inactive"),
        ("catalog.list","pending"),
    }, False),
    **dict.fromkeys({
        ("products.create","variants[].barcodes[].source"),
        ("variants.create","fields.barcodes[].source"),
        ("variants.batch_create","drafts[].barcodes[].source"),
        ("barcodes.add","source"),
    }, "store"),
    **dict.fromkeys({
        ("products.list","q"), ("catalog.list","q"),
        ("sales.list","date_from"), ("sales.list","date_to"),
        ("sales.list","payment"), ("sales.summary","date_from"),
        ("sales.summary","date_to"), ("sales.summary","payment"),
        ("sales.summary","date"), ("sales.export_save","date_from"),
        ("sales.export_save","date_to"), ("sales.export_save","payment"),
        ("sales.export","date_from"), ("sales.export","date_to"),
        ("sales.export","payment"),
    }, ""),
    **dict.fromkeys({
        ("products.update","fields"), ("variants.create","fields.attributes"),
        ("variants.update","fields"), ("variants.update_details","fields"),
    }, {}),
    ("variants.batch_create","drafts[].active"): 1,
}


def _set_path(payload, path, value):
    """Set a contract leaf path; [] addresses the first representative element."""
    parts = path.split(".")
    current = payload
    for part in parts[:-1]:
        if part.endswith("[]"):
            current = current[part[:-2]][0]
        else:
            current = current[part]
    leaf = parts[-1]
    if leaf.endswith("[]"):
        current[leaf[:-2]][0] = value
    else:
        current[leaf] = value


def _delete_path(payload, path):
    parts = path.split(".")
    current = payload
    for part in parts[:-1]:
        current = current[part[:-2]][0] if part.endswith("[]") else current[part]
    current.pop(parts[-1], None)


def _get_path(payload, path):
    current = payload
    for part in path.split("."):
        if part.endswith("[]"):
            current = current[part[:-2]][0]
        else:
            current = current[part]
    return current


def _sample(spec):
    rule = spec["range_enum"]
    if "YYYY-MM-DD" in rule: return "2026-08-10"
    if rule == "required|hidden": return "hidden"
    if rule == "select|text|multi|tags": return "select"
    if rule == "must be present in Setting.payments": return "現金"
    if rule == "must be None on create": return None
    kind = spec["type"]
    if kind == "str": return "sample"
    if kind == "bool|int": return True
    if kind == "int": return 1
    if kind == "bool": return False
    if kind == "list[int]": return [1]
    if kind == "mapping": return {}
    return []


def _positive_payload(base, path, spec):
    candidate = copy.deepcopy(base)
    try:
        _get_path(candidate, path)
    except (KeyError, IndexError):
        _set_path(candidate, path, _sample(spec))
    return candidate


def _constraint_invalid(spec):
    rule = spec["range_enum"]
    if rule == "none": return NA
    if rule == ">0": return 0
    if rule == ">=0": return -1
    if rule == "min_length=1": return []
    if rule == "0..qty*unit_price": return -1
    if rule == "must be None on create": return 1
    if rule == "must be present in Setting.payments": return "未設定付款"
    if "YYYY-MM-DD" in rule: return "2026/08/10"
    if "|" in rule: return "__invalid_enum__"
    return NA


class ActionContractTests(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.category_id = self.create_category("契約種類")
        self.field_id = self.create_field("顏色", self.category_id)
        self.invoke("options.create", {"field_id": self.field_id, "value": "黑"})
        self.option_id = self.invoke("options.list", {"field_id": self.field_id})[0]["option_id"]
        self.brand_id = self.invoke("brands.create", {"name": "契約廠牌"})["brand_id"]
        self.phone_brand_id = self.create_phone_brand("契約手機廠牌")
        self.model_id = self.create_model(self.phone_brand_id, "契約型號")
        product = self.invoke("products.create", {"name": "契約商品", "category_id": self.category_id, "variants": [{"attributes": {"顏色": "黑"}, "price": 100, "barcodes": [{"barcode": "contract-1"}]}]})
        self.product_id, self.variant_id = product["product_id"], product["variant_ids"][0]
        self.invoke("stock.receive", {"variant_id": self.variant_id, "qty": 3})
        self.session_id = self.invoke("stocktake.create", {})["session_id"]
        self.invoke("stocktake.scan", {"session_id":self.session_id, "variant_id":self.variant_id, "qty":1})
        self.delete_category_id = self.create_category("待刪種類")
        self.delete_brand_id = self.invoke("brands.create", {"name":"待刪廠牌"})["brand_id"]
        self.delete_phone_brand_id = self.create_phone_brand("待刪手機牌")
        self.delete_model_id = self.create_model(self.phone_brand_id, "待刪型號")
        self.invoke("options.create", {"field_id":self.field_id, "value":"待刪"})
        self.delete_option_id = next(row["option_id"] for row in self.invoke("options.list", {"field_id":self.field_id}) if row["value"] == "待刪")
        self.delete_product_id = self.invoke("products.create", {"name":"待刪商品","category_id":self.category_id,"variants":[]})["product_id"]
        self.delete_variant_id = self.invoke("variants.create", {"product_id":self.product_id,"fields":{}})["variant_id"]

    def valid_payload(self, action):
        values = {
            "categories.list": {}, "categories.create": {"name":"新增種類"}, "categories.update":{"id":self.category_id,"fields":{}}, "categories.delete":{"id":self.delete_category_id}, "categories.sort":{"ids":[]}, "categories.fields":{"id":self.category_id}, "categories.set_common_fields":{"id":self.category_id,"field_ids":[]}, "categories.set_field":{"category_id":self.category_id,"field_id":self.field_id,"fields":{}},
            "brands.list":{}, "brands.create":{"name":"新增廠牌"}, "brands.update":{"id":self.brand_id,"fields":{}}, "brands.delete":{"id":self.delete_brand_id}, "brands.sort":{"ids":[]}, "brands.set_categories":{"id":self.brand_id,"category_ids":[]},
            "phone_brands.list":{}, "phone_brands.create":{"name":"新增手機牌"}, "phone_brands.update":{"id":self.phone_brand_id,"fields":{}}, "phone_brands.delete":{"id":self.delete_phone_brand_id}, "phone_brands.sort":{"ids":[]},
            "models.list":{}, "models.create":{"phone_brand_id":self.phone_brand_id,"name":"新增型號"}, "models.update":{"id":self.model_id,"fields":{}}, "models.delete":{"id":self.delete_model_id}, "models.sort":{"ids":[]},
            "fields.list":{}, "fields.create":{"name":"尺寸"}, "fields.update":{"id":self.field_id,"fields":{}}, "options.list":{"field_id":self.field_id}, "options.create":{"field_id":self.field_id,"value":"白"}, "options.update":{"id":self.option_id,"fields":{}}, "options.delete":{"id":self.delete_option_id}, "options.models":{"id":self.option_id}, "options.set_models":{"id":self.option_id,"model_ids":[]}, "options.cleanup":{},
            "products.create":{"name":"新增商品","category_id":self.category_id,"variants":[{"attributes":{},"price":100,"model_ids":[],"barcodes":[{"barcode":"new-code","source":"store"}]}]}, "products.list":{}, "products.update":{"id":self.product_id,"fields":{}}, "products.delete":{"id":self.delete_product_id}, "catalog.list":{},
            "variants.create":{"product_id":self.product_id,"fields":{"attributes":{},"price":100,"model_ids":[],"barcodes":[{"barcode":"variant-new","source":"store"}]}}, "variants.update":{"id":self.variant_id,"fields":{}}, "variants.set_models":{"id":self.variant_id,"model_ids":[]}, "variants.update_details":{"id":self.variant_id,"fields":{},"model_ids":[]}, "variants.delete":{"id":self.delete_variant_id}, "variants.batch_create":{"product_id":self.product_id,"drafts":[{"draft_id":"d1","attributes":{"顏色":"白"},"price":100,"active":1,"model_ids":[],"barcodes":[{"barcode":"batch-new","source":"store"}]}]}, "variants.field_usage":{"category_id":self.category_id,"field_id":self.field_id}, "variants.activate":{"id":self.variant_id}, "variants.issues":{},
            "barcodes.scan":{"code":"contract-1"}, "barcodes.add":{"variant_id":self.variant_id,"barcode":"contract-2"}, "barcodes.delete":{"code":"contract-1"}, "stock.receive":{"variant_id":self.variant_id,"qty":1}, "stock.detail":{"variant_id":self.variant_id},
            "stocktake.create":{}, "stocktake.list":{}, "stocktake.detail":{"session_id":self.session_id}, "stocktake.scan":{"session_id":self.session_id,"variant_id":self.variant_id,"qty":1}, "stocktake.set_counted":{"session_id":self.session_id,"variant_id":self.variant_id,"counted_qty":1}, "stocktake.close":{"session_id":self.session_id},
            "payments.list":{}, "sales.checkout":{"payment":"現金","paid":100,"items":[{"variant_id":self.variant_id,"qty":1,"unit_price":100}]}, "sales.list":{}, "sales.summary":{}, "sales.export_save":{}, "printing.barcode":{"variant_id":self.variant_id},
        }
        return values[action]

    def _prepare(self, action, payload):
        if action == "sales.export_save":
            return self.facade.sales._prepare_payload("sales.export", payload)
        if action == "printing.barcode":
            return self.facade.printing.invoke(action, payload)
        for facade in (self.facade.settings, self.facade.products, self.facade.stock,
                       self.facade.sales, self.facade.stocktake):
            if action in facade.ACTIONS:
                return facade._prepare_payload(action, payload)
        self.fail(f"unmapped action {action}")

    def test_contract_is_complete_and_matches_frontend_and_facades(self):
        with open("static/js/api.js", encoding="utf-8") as source:
            declared = re.search(r"const allowed = new Set\(\[(.*?)\]\);", source.read(), re.S).group(1)
        browser_actions = set(re.findall(r'"([^\"]+)"', declared))
        direct = set().union(self.facade.settings.ACTIONS, self.facade.products.ACTIONS,
                             self.facade.stock.ACTIONS, self.facade.sales.ACTIONS,
                             self.facade.stocktake.ACTIONS, self.facade.printing.ACTIONS)
        self.assertEqual(len(ACTION_CONTRACTS), 65)
        self.assertEqual(browser_actions, FRONTEND_ACTIONS)
        self.assertEqual(direct, (FRONTEND_ACTIONS - {"sales.export_save"}) | {"sales.export"})
        self.assertEqual(set(INTERNAL_ACTION_CONTRACTS), {"sales.export"})
        self.assertEqual(set(TARGET_BY_ACTION), FRONTEND_ACTIONS | {"sales.export"})
        for action, contract in {**ACTION_CONTRACTS, **INTERNAL_ACTION_CONTRACTS}.items():
            with self.subTest(action=action):
                self.assertEqual(contract["extra_fields"], "reject")
                for path, spec in contract["fields"].items():
                    self.assertEqual(path, spec["payload_path"])
                    self.assertEqual(set(spec), {"payload_path","required","type","nullable","default","range_enum","bool_policy","normalization","source","target","cases"})
                    self.assertNotEqual(spec["type"], "any")
                    self.assertIn("lib.", spec["target"])
                    self.assertNotEqual(spec["target"], "Facade._prepare_payload")

    def test_source_metadata_matches_exact_fastapi_registry_and_rejects_bogus_labels(self):
        contracts = {**ACTION_CONTRACTS, **INTERNAL_ACTION_CONTRACTS}
        validate_source_metadata(contracts)

        bogus = copy.deepcopy(contracts)
        bogus["stock.detail"]["fields"]["variant_id"]["source"] = "api.stock.Bogus"
        with self.assertRaisesRegex(AssertionError, "unknown source"):
            validate_source_metadata(bogus)

        hidden_http_source = copy.deepcopy(contracts)
        hidden_http_source["stock.detail"]["fields"]["variant_id"]["source"] = "none"
        with self.assertRaisesRegex(AssertionError, "wrong source"):
            validate_source_metadata(hidden_http_source)

        invented_facade_source = copy.deepcopy(contracts)
        invented_facade_source["catalog.list"]["fields"]["pending"]["source"] = \
            "api.products.catalog.q"
        with self.assertRaisesRegex(AssertionError, "wrong source"):
            validate_source_metadata(invented_facade_source)

    def test_metadata_cases_execute_against_real_facade_validators(self):
        for action, contract in ACTION_CONTRACTS.items():
            base = self.valid_payload(action)
            # Printing's valid call intentionally raises unsupported; invalid cases
            # still prove shape validation, and it has no optional acceptance cases.
            for path, spec in contract["fields"].items():
                cases = spec["cases"]
                candidate = copy.deepcopy(base)
                try: _delete_path(candidate, path)
                except (KeyError, IndexError): candidate = None
                if candidate is not None:
                    with self.subTest(action=action, path=path, case="missing"):
                        if cases["missing"] == "reject":
                            with self.assertRaises(ValidationError): self._prepare(action, candidate)
                        elif action != "printing.barcode":
                            self._prepare(action, candidate)
                try: representative = _positive_payload(base, path, spec)
                except (KeyError, IndexError): representative = None
                if representative is not None:
                    with self.subTest(action=action, path=path, case="correct_type"):
                        if action == "printing.barcode":
                            with self.assertRaisesRegex(ValidationError, "列印功能尚未支援"):
                                self._prepare(action, representative)
                        else:
                            self._prepare(action, representative)
                    candidate = copy.deepcopy(representative); _set_path(candidate, path, cases["wrong_type"]["value"])
                    with self.subTest(action=action, path=path, case="wrong_type"):
                        with self.assertRaises(ValidationError): self._prepare(action, candidate)
                    if cases["bool_int"] == "reject":
                        candidate = copy.deepcopy(representative); _set_path(candidate, path, True)
                        with self.subTest(action=action, path=path, case="bool_int"):
                            with self.assertRaises(ValidationError): self._prepare(action, candidate)
                    if cases["null"] in ("accept", "reject"):
                        candidate = copy.deepcopy(representative); _set_path(candidate, path, None)
                        with self.subTest(action=action, path=path, case="null"):
                            if cases["null"] == "reject":
                                with self.assertRaises(ValidationError): self._prepare(action, candidate)
                            elif action != "printing.barcode": self._prepare(action, candidate)
                    if cases["blank"] in ("accept", "reject"):
                        candidate = copy.deepcopy(representative); _set_path(candidate, path, "   ")
                        with self.subTest(action=action, path=path, case="blank"):
                            if cases["blank"] == "reject":
                                with self.assertRaises(ValidationError):
                                    if action == "sales.checkout" and path == "payment":
                                        self.invoke(action, candidate)
                                    else:
                                        self._prepare(action, candidate)
                            else: self._prepare(action, candidate)
                    if isinstance(cases["list_element"], dict):
                        candidate = copy.deepcopy(representative)
                        _set_path(candidate, path, [cases["list_element"]["value"]])
                        with self.subTest(action=action, path=path, case="list_element"):
                            with self.assertRaises(ValidationError): self._prepare(action, candidate)
        # Every action gets the otherwise-valid + extra mutation.
        for action in ACTION_CONTRACTS:
            candidate = copy.deepcopy(self.valid_payload(action)); candidate["unexpected"] = True
            with self.subTest(action=action, case="extra"):
                with self.assertRaises(ValidationError): self._prepare(action, candidate)

    def test_declared_range_and_enum_cases_execute(self):
        for action, contract in ACTION_CONTRACTS.items():
            base = self.valid_payload(action)
            for path, spec in contract["fields"].items():
                invalid = _constraint_invalid(spec)
                if invalid == NA: continue
                candidate = copy.deepcopy(base)
                try: _set_path(candidate, path, invalid)
                except (KeyError, IndexError): continue
                with self.subTest(action=action, path=path, constraint=spec["range_enum"]):
                    if action == "sales.export_save":
                        with self.assertRaises(ValidationError):
                            self.facade.sales._prepare_payload("sales.export", candidate)
                    else:
                        with self.assertRaises(ValidationError): self.invoke(action, candidate)

    def test_every_accept_bool_field_executes_true(self):
        declared = {(action,path) for action,contract in ACTION_CONTRACTS.items()
                    for path,spec in contract["fields"].items()
                    if spec["bool_policy"] == "accept_bool"}
        executed = set()
        for action,path in declared:
            candidate = copy.deepcopy(self.valid_payload(action))
            _set_path(candidate, path, True)
            with self.subTest(action=action, path=path):
                self.assertIs(_get_path(candidate, path), True)
                self._prepare(action, candidate)
                executed.add((action,path))
        self.assertEqual(executed, declared)

    def test_nested_extras_and_checkout_semantics_are_rejected(self):
        cases = []
        payload = self.valid_payload("categories.update")
        payload["fields"]["unexpected"] = 1
        cases.append(("categories.update", payload, "fields"))
        payload = self.valid_payload("products.create")
        payload["variants"][0]["unexpected"] = 1
        cases.append(("products.create", payload, "variants[]"))
        payload = self.valid_payload("products.create")
        payload["variants"][0]["barcodes"][0]["unexpected"] = 1
        cases.append(("products.create", payload, "barcodes[]"))
        payload = self.valid_payload("variants.batch_create")
        payload["drafts"][0]["unexpected"] = 1
        cases.append(("variants.batch_create", payload, "drafts[]"))
        payload = self.valid_payload("variants.batch_create")
        payload["drafts"][0]["barcodes"][0]["unexpected"] = 1
        cases.append(("variants.batch_create", payload, "drafts[].barcodes[]"))
        payload = self.valid_payload("sales.checkout")
        payload["items"][0]["unexpected"] = 1
        cases.append(("sales.checkout", payload, "items[]"))
        for action, candidate, location in cases:
            with self.subTest(action=action, location=location):
                with self.assertRaises(ValidationError): self._prepare(action, candidate)

        with self.assertRaisesRegex(ValidationError, "不支援的欄位：unexpected"):
            self.invoke("printing.barcode", {"variant_id":self.variant_id, "unexpected":True})
        with self.assertRaisesRegex(ValidationError, "單項折扣不可超過"):
            self.invoke("sales.checkout", {
                "payment":"現金", "paid":100,
                "items":[{"variant_id":self.variant_id,"qty":1,"unit_price":100,"discount":101}],
            })
        with self.assertRaisesRegex(ValidationError, "付款方式未在設定中"):
            self.invoke("sales.checkout", {
                "payment":"   ", "paid":100,
                "items":[{"variant_id":self.variant_id,"qty":1,"unit_price":100}],
            })

    def assert_valid_action(self, action):
        contract = ACTION_CONTRACTS[action]
        if contract["valid_error"] == "validation_error":
            with self.assertRaisesRegex(ValidationError, "尚未支援"):
                self.invoke(action, self.valid_payload(action))
        elif action == "sales.export_save":
            self.facade.sales._prepare_payload("sales.export", self.valid_payload(action))
        else:
            self.invoke(action, self.valid_payload(action))

    def test_expected_domain_errors_are_semantically_explicit(self):
        cases = (
            ("stock.detail", {"variant_id": 999999}, NotFoundError),
            ("products.delete", {"id": 999999}, NotFoundError),
            ("stocktake.close", {"session_id": 999999}, NotFoundError),
        )
        for action, payload, error in cases:
            with self.subTest(action=action):
                with self.assertRaises(error): self.invoke(action, payload)
        used = self.create_category("有商品種類")
        self.invoke("products.create", {"name":"占用","category_id":used,"variants":[]})
        with self.assertRaises(ConflictError):
            self.invoke("categories.delete", {"id":used})

    def test_declared_defaults_and_normalizations_have_observable_examples(self):
        declared = {(action,path) for action,contract in ACTION_CONTRACTS.items()
                    for path,spec in contract["fields"].items()
                    if spec["normalization"] != "none"}
        self.assertEqual(declared, NORMALIZATION_PROBE_PATHS)
        self.assertEqual(
            self.facade.sales._prepare_payload("sales.list", {}),
            {"date_from":"", "date_to":"", "payment":""},
        )
        checkout = self.facade.sales._prepare_payload("sales.checkout", {
            "payment":"現金", "paid":100,
            "items":[{"variant_id":self.variant_id,"qty":1,"unit_price":100}],
        })
        self.assertEqual(checkout["order_discount"], 0)
        self.assertEqual(checkout["items"][0]["discount"], 0)
        created = self.invoke("models.create", {
            "phone_brand_id":self.phone_brand_id, "name":"series trim", "series":"   ",
        })
        row = next(item for item in self.invoke("models.list", {"all":1})
                   if item["model_id"] == created["model_id"])
        self.assertIsNone(row["series"])
        self.invoke("models.update", {"id":self.model_id, "fields":{"series":"  "}})
        row = next(item for item in self.invoke("models.list", {"all":1})
                   if item["model_id"] == self.model_id)
        self.assertIsNone(row["series"])
        summary = self.facade.sales._prepare_payload("sales.summary", {"date":"2026-08-10"})
        self.assertEqual(summary, {"date_from":"2026-08-10", "date_to":"2026-08-10", "payment":""})

        with self.assertRaises(ConflictError):
            self.invoke("products.create", {
                "name":"  契約商品  ", "category_id":self.category_id, "variants":[],
            })
        inline = self.invoke("products.create", {
            "name":"品牌正規化", "category_id":self.category_id,
            "brand_name":"  Inline   Brand  ",
            "variants":[{"attributes":{"顏色":[" 黑 ","黑"]}, "barcodes":[{"barcode":None}]}],
        })
        brands = self.invoke("brands.list", {"all":1})
        self.assertIn("Inline Brand", {item["name"] for item in brands})
        inline_vid = inline["variant_ids"][0]
        with get_conn(self.db) as conn:
            generated = conn.execute(
                "SELECT barcode FROM Barcode WHERE variant_id=?", (inline_vid,)).fetchone()[0]
            self.assertRegex(generated, r"^TL\d+$")
            normalized_option = conn.execute(
                "SELECT o.value FROM VariantAttribute va JOIN AttributeOption o "
                "ON o.option_id=va.option_id WHERE va.variant_id=?", (inline_vid,)).fetchone()[0]
            self.assertEqual(normalized_option, "黑")

        # Missing fields is normalized by dispatch to an empty update mapping.
        self.assertEqual(self.invoke("products.update", {"id":self.product_id}), {"ok":True})
        batch = self.invoke("variants.batch_create", {
            "product_id":self.product_id,
            "drafts":[{
                "draft_id":"normalized", "attributes":{"顏色":[" 白 ", "白"]},
                "active":0, "model_ids":[self.model_id,self.model_id],
                "barcodes":[{"barcode":" batch-trim ", "source":""}],
            }],
        })
        batch_result = batch["results"][0]
        self.assertEqual(batch_result["barcodes"], [{"barcode":"batch-trim", "source":"factory"}])
        with get_conn(self.db) as conn:
            vid = batch_result["variant_id"]
            self.assertEqual(conn.execute("SELECT active FROM Variant WHERE variant_id=?", (vid,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM VariantModel WHERE variant_id=?", (vid,)).fetchone()[0], 1)
            normalized_option = conn.execute(
                "SELECT o.value FROM VariantAttribute va JOIN AttributeOption o "
                "ON o.option_id=va.option_id WHERE va.variant_id=?", (vid,)).fetchone()[0]
            self.assertEqual(normalized_option, "白")

    def test_every_declared_default_executes_and_key_defaults_are_stored(self):
        declared = {(action,path):spec["default"]
                    for action,contract in {**ACTION_CONTRACTS, **INTERNAL_ACTION_CONTRACTS}.items()
                    for path,spec in contract["fields"].items()
                    if spec["default"] != "missing"}
        self.assertEqual(len(declared), 85)
        self.assertEqual(declared, EXPECTED_DEFAULTS)
        executed = {}
        for (action,path), expected in EXPECTED_DEFAULTS.items():
            candidate = copy.deepcopy(self.valid_payload(
                "sales.export_save" if action == "sales.export" else action))
            _delete_path(candidate, path)
            with self.subTest(action=action, path=path, default=expected):
                prepared = self._prepare(action, candidate)
                try:
                    actual = _get_path(prepared, path)
                except (KeyError, IndexError, TypeError):
                    # This validator intentionally preserves omission.  Prove
                    # its exact output and that the declared literal survives
                    # the same real validator when supplied explicitly.
                    if (action, path) == ("sales.summary", "date"):
                        self.assertEqual(
                            prepared,
                            {"date_from":"", "date_to":"", "payment":""},
                        )
                    else:
                        self.assertEqual(prepared, candidate)
                    explicit = copy.deepcopy(candidate)
                    _set_path(explicit, path, copy.deepcopy(expected))
                    explicit_prepared = self._prepare(action, explicit)
                    if (action, path) == ("sales.summary", "date"):
                        self.assertEqual(
                            explicit_prepared,
                            {"date_from":"", "date_to":"", "payment":""},
                        )
                    else:
                        self.assertEqual(_get_path(explicit_prepared, path), expected)
                    executed[(action,path)] = "exact_validator_omission"
                else:
                    self.assertEqual(actual, expected)
                    executed[(action,path)] = "materialized_by_validator"
        self.assertEqual(set(executed), set(EXPECTED_DEFAULTS))
        self.assertNotIn("skipped", executed.values())
        self.assertEqual(
            {strategy: list(executed.values()).count(strategy) for strategy in set(executed.values())},
            {"materialized_by_validator":14, "exact_validator_omission":71},
        )

        field_id = self.invoke("fields.create", {"name":"預設型態"})["field_id"]
        field_row = next(row for row in self.invoke("fields.list", {})
                         if row["field_id"] == field_id)
        self.assertEqual(field_row["field_type"], "select")

        generated = self.invoke("barcodes.add", {"variant_id":self.variant_id})["barcode"]
        with get_conn(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT source FROM Barcode WHERE barcode=?", (generated,)).fetchone()[0], "store")

        product = self.invoke("products.create", {
            "name":"預設空變體", "category_id":self.category_id,
        })
        with get_conn(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM Variant WHERE product_id=?", (product["product_id"],)).fetchone()[0], 0)

        batch = self.invoke("variants.batch_create", {
            "product_id":self.product_id,
            "drafts":[{"draft_id":"default-active", "attributes":{"顏色":"白"}}],
        })
        with get_conn(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT active FROM Variant WHERE variant_id=?",
                (batch["results"][0]["variant_id"],)).fetchone()[0], 1)

    def test_desktop_bridge_export_save_forwards_payload_unchanged(self):
        class ExportFacade:
            def __init__(self): self.calls = []
            def invoke(self, action, payload):
                self.calls.append((action, payload)); return {"filename":"sales.csv","content":"csv"}
        class Window:
            def create_file_dialog(self, *_args, **_kwargs): return None
        facade = ExportFacade(); payload = {"payment":"現金"}
        response = DesktopBridge(facade=facade, window=Window(), save_dialog_type="SAVE").invoke("sales.export_save", payload)
        self.assertEqual(response, {"ok":True,"data":{"cancelled":True}})
        self.assertEqual(facade.calls, [("sales.export", payload)])


def _install_isolated_valid_action_tests():
    """One unittest instance/database per action; no shared destructive loop."""
    for action in ACTION_CONTRACTS:
        name = "test_valid_" + action.replace(".", "_")
        def test(self, selected=action):
            self.assert_valid_action(selected)
        test.__name__ = name
        setattr(ActionContractTests, name, test)


_install_isolated_valid_action_tests()


class HttpFacadeComparisonTests(ApiTestCase):
    """Temporary phase-3/4 parity checks; delete with api/ in phase 5."""
    def setUp(self):
        super().setUp(); self.facade = DesktopFacade(self.db)

    def test_success_validation_not_found_and_conflict_semantics_match(self):
        cid = self.c.post("/api/categories", json={"name":"HTTP parity"}).json()["category_id"]
        self.assertEqual(self.c.get("/api/categories").json(), self.facade.invoke("categories.list", {"all":0}))

        # Same strict-body mutation: HTTP 422 and Facade validation_error.
        response = self.c.post("/api/stock/receive", json={"variant_id":True,"qty":1})
        self.assertEqual(response.status_code, 422)
        with self.assertRaises(ValidationError): self.facade.invoke("stock.receive", {"variant_id":True,"qty":1})

        response = self.c.get("/api/stock/999999")
        self.assertEqual(response.status_code, 404)
        with self.assertRaises(NotFoundError): self.facade.invoke("stock.detail", {"variant_id":999999})

        self.c.post("/api/products", json={"name":"占用","category_id":cid,"variants":[]})
        response = self.c.delete(f"/api/categories/{cid}")
        self.assertEqual(response.status_code, 409)
        with self.assertRaises(ConflictError): self.facade.invoke("categories.delete", {"id":cid})

    def test_printing_http_contract_remains_legacy_while_desktop_is_strict(self):
        response = self.c.post("/api/print/barcode", json={"barcode":"4711", "name":"商品"})
        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.json()["detail"], "條碼列印尚未接上實體印表機")
        with self.assertRaisesRegex(ValidationError, "列印功能尚未支援"):
            self.facade.invoke("printing.barcode", {"variant_id":1})

    def test_representative_pydantic_coercion_differences_are_catalogued(self):
        # Legacy Pydantic bodies coerce numeric strings for non-strict catalogue
        # models; Desktop is intentionally strict. Strict stock/sales models align.
        response = self.c.post("/api/categories", json={"name":"coerced","sort":"1"})
        self.assertEqual(response.status_code, 200)
        with self.assertRaises(ValidationError):
            self.facade.invoke("categories.create", {"name":"coerced","sort":"1"})
        strict = self.c.post("/api/sales", json={"payment":"現金","paid":"0","items":[]})
        self.assertEqual(strict.status_code, 422)
        with self.assertRaises(ValidationError):
            self.facade.invoke("sales.checkout", {"payment":"現金","paid":"0","items":[]})

        extra = self.c.post("/api/categories", json={"name":"extra ignored","unexpected":True})
        self.assertEqual(extra.status_code, 200)
        with self.assertRaises(ValidationError):
            self.facade.invoke("categories.create", {"name":"extra rejected","unexpected":True})
        self.assertEqual({item["kind"] for item in PYDANTIC_DIFFERENCES},
                         {"coercion", "extras", "nullable", "default", "separate_contract"})

    def test_nullable_and_default_pydantic_differences_execute_both_validators(self):
        cid = self.c.post("/api/categories", json={"name":"Pydantic 差異"}).json()["category_id"]
        created = self.c.post("/api/products", json={
            "name":"差異商品", "category_id":cid, "variants":[{"price":10}],
        }).json()
        vid = created["variant_ids"][0]

        nullable_payload = {"attributes":None}
        response = self.c.put(f"/api/variants/{vid}", json=nullable_payload)
        self.assertEqual(response.status_code, 422)
        self.assertIn("規格 格式不正確", response.json()["detail"])
        with self.assertRaises(ValidationError):
            self.facade.invoke("variants.update", {"id":vid, "fields":nullable_payload})

        sid = self.c.post("/api/stocktake", json={}).json()["session_id"]
        default_payload = {"variant_id":vid}
        response = self.c.post(f"/api/stocktake/{sid}/scan", json=default_payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["counted_qty"], 1)
        with self.assertRaises(ValidationError):
            self.facade.invoke("stocktake.scan", {"session_id":sid, **default_payload})

    def test_export_route_body_matches_internal_facade_export(self):
        response = self.c.get("/api/sales/export")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, self.facade.invoke("sales.export", {})["content"])
