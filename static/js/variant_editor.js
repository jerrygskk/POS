window.VariantEditorApp = {
  data() {
    return {
      loading: true,
      ready: false,
      saving: false,
      committed: false,
      error: "",
      barcodeError: "",
      product: null,
      variant: null,
      fields: [],
      fieldOptions: {},
      fieldUsage: {},
      models: [],
      attrs: {},
      modelIds: [],
      price: null,
      barcodes: [],
      deletedBarcodes: [],
      factoryBarcode: "",
      pendingStoreCount: 0,
    };
  },
  async mounted() {
    this._keydown = (event) => this.handleKeydown(event);
    document.addEventListener("keydown", this._keydown);
    await this.load();
  },
  unmounted() {
    document.removeEventListener("keydown", this._keydown);
  },
  methods: {
    async load() {
      this.loading = true;
      this.ready = false;
      this.error = "";
      try {
        const context = await API.invoke("desktop.child_window.context", {});
        const product = context.context.product;
        const variant = context.context.variant;
        const [fields, models] = await Promise.all([
          API.categoryFields(product.category_id),
          API.listModels({}),
        ]);
        const fieldOptions = {};
        const fieldUsage = {};
        await window.CatalogFields.loadFieldsWithOptions(
          fields, fieldOptions);
        await window.CatalogFields.loadFieldUsage(
          product.category_id, fields, fieldUsage, ["select", "multi", "tags"]);
        const attrs = window.initFormAttrs(fields, variant.attributes || {});
        const modelIds = this.modelIdsByNames(variant.models || [], models);
        const barcodes = (variant.barcodes || []).map(barcode => ({
          ...barcode, existing: true,
        }));
        Object.assign(this, {
          product, variant, fields, models, fieldOptions, fieldUsage, attrs,
          modelIds, price: variant.price, barcodes,
        });
        this.ready = true;
      } catch (error) {
        this.error = error.message;
      } finally {
        this.loading = false;
      }
    },
    modelIdsByNames(names, models) {
      models = models || this.models;
      const ids = [];
      for (const name of names) {
        const model = models.find(item => (item.alias || item.name) === name)
          || models.find(item => item.name === name);
        if (model) ids.push(model.model_id);
      }
      return ids;
    },
    removeBarcode(barcode) {
      const index = this.barcodes.indexOf(barcode);
      if (index < 0) return;
      if (barcode.existing) this.deletedBarcodes.push(barcode.barcode);
      this.barcodes.splice(index, 1);
      this.barcodeError = "";
    },
    addFactoryBarcode() {
      const barcode = this.factoryBarcode.trim();
      if (!barcode) {
        this.barcodeError = "請輸入原廠條碼";
        return;
      }
      if (barcode.toUpperCase().startsWith("TL")) {
        this.barcodeError = "TL 開頭為系統保留，如有需求請新增自取碼";
        return;
      }
      if (this.barcodes.some(item => item.barcode === barcode)) {
        this.barcodeError = "條碼已在清單中";
        return;
      }
      this.barcodes.push({barcode, source: "factory", existing: false});
      this.factoryBarcode = "";
      this.barcodeError = "";
    },
    handleFactoryBarcodeEnter(event) {
      event.preventDefault();
      event.stopPropagation();
      this.addFactoryBarcode();
    },
    addStoreBarcode() {
      this.pendingStoreCount += 1;
      this.barcodeError = "";
    },
    async save() {
      if (this.saving || !this.ready) return;
      this.saving = true;
      this.error = "";
      try {
        await API.updateVariantEditor({
          id: this.variant.variant_id,
          fields: {
            attributes: window.buildAttrPayload(this.fields, this.attrs),
            price: this.price === "" ? null : (this.price ?? null),
          },
          model_ids: this.modelIds,
          deleted_barcodes: this.deletedBarcodes,
          factory_barcodes: this.barcodes
            .filter(item => !item.existing).map(item => item.barcode),
          store_barcode_count: this.pendingStoreCount,
        });
        this.committed = true;
        await API.invoke("desktop.child_window.close", {saved: true});
      } catch (error) {
        this.error = error.message;
      } finally {
        this.saving = false;
      }
    },
    async cancel() {
      if (this.saving) return;
      await API.invoke("desktop.child_window.close", {saved: this.committed});
    },
    async handleKeydown(event) {
      if (event.key === "Escape") await this.cancel();
    },
  },
};

document.addEventListener("DOMContentLoaded", () => {
  const app = Vue.createApp(window.VariantEditorApp);
  for (const [name, component] of Object.entries(window.PosComponents || {}))
    app.component(name, component);
  app.mount("#variant-editor");
});
