window.PosPages = window.PosPages || {};

// 廠牌／手機品牌／型號沿用通用清單維護;種類與產品改走商品設定單頁。
const _MAINT = {
  brands: { id: "brand_id", label: "廠牌", list: "listBrands", create: "createBrand", update: "updateBrand", delete: "deleteBrand", sort: "sortBrands" },
  phoneBrands: { id: "phone_brand_id", label: "手機品牌", list: "listPhoneBrands", create: "createPhoneBrand", update: "updatePhoneBrand", delete: "deletePhoneBrand", sort: "sortPhoneBrands" },
  models: { id: "model_id", label: "型號", list: "listModels", create: "createModel", update: "updateModel", delete: "deleteModel", sort: "sortModels" },
};

// 規格模板固定列:每個種類都有一列「手機型號」,點列切換該種類是否使用適用型號
// (讀寫 Category.model_mode)。型號實際存於 VariantModel 關聯表,不是規格欄,
// 故以固定列呈現而非真的建 AttributeField。
const MODEL_ROW_ID = "__model__";
const FEATURE_ROW_ID = "__feature__";   // 該種類尚未啟用特性詞條時的佔位列
// 新種類自動建的規格欄(每個種類各自一份,不與別種類共用);
// 與 lib/db_seed.NEW_CATEGORY_OWN_FIELDS 對齊
const DEFAULT_OWN_FIELDS = [["顏色", "select"], ["款式", "select"]];

window.PosPages["page-settings"] = {
  template: "#tpl-settings",
  // 錯誤訊息顯示在目前這一區塊內(與未儲存提醒條同一個位置),不再跳到整頁最上面
  // ——訊息離發生的地方太遠,店員看不到自己剛剛按的那顆鈕出了什麼事。
  data() {
    return {
      categories: [], brands: [], phoneBrands: [], models: [],
      newItem: { brands: "", phoneBrands: "" },
      newModel: { phone_brand_id: null, name: "", series: "" },
      newSeq: { brands: "", phoneBrands: "", models: "" },
      // 商品設定單頁
      section: "category",
      selCatId: null, newCatName: "",
      tplFields: [], bigProducts: [],
      newField: { name: "", field_type: "select" },
      // 規格模板 popup(單層)
      // 產品 popup(單層)
      prodPopup: null,
      // 廠牌經營種類
      openBrand: null, openBrandName: "", brandCatChecked: {},
      snap: {},   // 載入時的名稱快照,用來判斷哪幾筆被改過(提醒條與批次儲存共用)
      pendingSort: {},       // kind → 拖過但還沒儲存的新順序(廠牌、手機品牌)
      pendingModelSort: {},  // 品牌 → 該品牌型號拖過但還沒儲存的新順序
      pageError: "", errorScope: "category",  // 錯誤訊息與它屬於哪個區塊
      modelQuery: "",        // 型號搜尋字串
      collapsedBrands: {},   // 手動收合的品牌(brand → true)
    };
  },
  computed: {
    // 型號依品牌分組;有搜尋字串時只留符合的型號(比對名稱、別名、系列),
    // 沒有任何符合的品牌整組不顯示。
    modelGroups() {
      const q = this.modelQuery.trim().toLowerCase();
      const match = (m) => !q || [m.name, m.alias, m.series]
        .some(v => String(v || "").toLowerCase().includes(q));
      const g = {};
      for (const m of this.models)
        if (match(m)) (g[m.brand_name] = g[m.brand_name] || []).push(m);
      return Object.keys(g).map(brand => ({ brand, items: g[brand] }));
    },
    // 名稱正由廠牌自動帶入(還沒被手動改過)
    autoNameActive() {
      const p = this.prodPopup;
      return !!(p && !p.nameDirty && this.autoName());
    },
    // 名稱被改過、但目前廠牌算得出自動名稱且與現在不同 → 提供還原
    canRestoreAutoName() {
      const p = this.prodPopup;
      if (!p || !p.nameDirty) return false;
      const auto = this.autoName();
      return !!auto && auto !== (p.name || "").trim();
    },
    // 撞名提示:同種類已有同名產品(修改時排除自己)。
    // 同廠牌多條產品線時自動名稱會一樣,當下就講,不要等按了儲存才被後端擋。
    duplicateNameWarning() {
      const p = this.prodPopup;
      if (!p) return "";
      const name = (p.name || "").trim().toLowerCase();
      if (!name) return "";
      const hit = this.bigProducts.some(
        x => x.product_id !== p.product_id
          && (x.name || "").trim().toLowerCase() === name);
      return hit
        ? `「${(p.name || "").trim()}」已存在，若有其他產品線請加上系列名稱`
        : "";
    },
    selectedCat() {
      return this.categories.find(c => c.category_id === this.selCatId) || null;
    },
    // 固定列:手機型號與特性詞條,兩者都只是「這個種類要不要用」的開關。
    // 特性詞條每個種類各自一份,沒有就補一列佔位(顯示未使用)。
    fixedRows() {
      if (!this.selectedCat) return [];
      const feat = this.tplFields.find(f => this.isFeature(f)) ||
        { field_id: FEATURE_ROW_ID, name: "特性詞條", field_type: "tags",
          required: 0, cf_active: 0, default_option_id: null, sort: -1 };
      const on = this.selectedCat.model_mode === "required";
      return [
        { field_id: MODEL_ROW_ID, name: "手機型號", field_type: "model",
          required: on, cf_active: on, default_option_id: null, sort: -1 },
        feat,
      ];
    },
    // 自訂規格:可拖拉排序、可在列上直接改名稱／型態／必填／啟用
    customRows() {
      return this.tplFields.filter(f => !this.isFeature(f))
        .sort((a, b) => (a.sort - b.sort) || (a.field_id - b.field_id));
    },
  },
  async mounted() {
    this._loadSeq = 0;
    // 規格設定子視窗存檔後關窗:重讀該種類模板(沒存檔就關掉不白跑一次查詢)
    this._childWindowClosed = (event) => this.onChildWindowClosed(event);
    window.addEventListener("pos-child-window-closed", this._childWindowClosed);
    await this.reloadAll();
  },
  unmounted() {
    window.removeEventListener("pos-child-window-closed", this._childWindowClosed);
  },
  methods: {
    // scope 決定訊息出現在哪一段(category／brands／phoneBrands／models),
    // 不指定就歸商品種類那段;沒有歸屬會四個區塊同時亮。
    showError(message, scope) {
      this.errorScope = scope || this.errorScope || "category";
      this.pageError = message;
      clearTimeout(this._errorTimer);
      this._errorTimer = setTimeout(() => { this.pageError = ""; }, 5000);
    },
    async reloadAll() {
      await this.guard(async () => {
        this.categories = await API.listCategories({ all: 1 });
        this.brands = await API.listBrands({ all: 1 });
        this.phoneBrands = await API.listPhoneBrands({ all: 1 });
        this.models = await API.listModels({ all: 1 });
        this._takeSnap();
        if (!this.categories.some(c => c.category_id === this.selCatId))
          this.selCatId = this.categories.length ? this.categories[0].category_id : null;
        if (this.selCatId != null) await this.loadCategoryDetail();
      });
    },

    // ==== 商品設定:種類 ====
    selectSection(name) {
      this.section = name;
      this.prodPopup = null;
    },
    async selectCategory(c) {
      const same = this.selCatId === c.category_id;
      this.section = "category";
      this.prodPopup = null;
      if (same) return;
      this.selCatId = c.category_id;
      await this.guard(() => this.loadCategoryDetail());
    },
    async loadCategoryDetail() {
      const seq = ++this._loadSeq;
      const cid = this.selCatId;
      const fields = await API.listFields({ category_id: cid });
      const products = await API.listCatalog({ category_id: cid, include_inactive: true });
      if (seq !== this._loadSeq) return;
      this.tplFields = fields;
      this._fieldNameSnap = Object.fromEntries(fields.map(f => [f.field_id, f.name]));
      this.bigProducts = products;
    },
    async addCategory() {
      this.errorScope = "category";
      const name = (this.newCatName || "").trim();
      if (!name) { this.showError("請輸入商品種類名稱"); return; }
      await this.guard(async () => {
        const r = await API.createCategory({ name, model_mode: "hidden" });
        await this.attachDefaultFields(r.category_id);
        this.newCatName = "";
        this.selCatId = r.category_id;
        await this.reloadAll();
      });
    },
    // 新種類預設帶「顏色」「款式」兩個規格欄(選填):空白模板讓店員無從下手。
    // 兩個都是本種類自己一份——同名欄在不同種類的詞彙互不相干(手機殼的天峰藍
    // vs 傳輸線的黑白),共用會讓建檔候選混進別種類的值。不需要的用紅色 ✕ 移除。
    async attachDefaultFields(categoryId) {
      let sort = 1;
      for (const [name, fieldType] of DEFAULT_OWN_FIELDS) {
        const created = await API.createField(
          { name, field_type: fieldType, category_id: categoryId });
        await API.setCategoryField(categoryId, created.field_id,
                                   { sort: sort++, required: 0, active: 1 });
      }
    },
    async saveCategoryName(c) {
      const name = (c.name || "").trim();
      if (!name) { this.showError("商品種類名稱不可空白"); return; }
      await this.guard(() => API.updateCategory(c.category_id, { name }));
    },
    async toggleCategoryActive(c) {
      await this.guard(async () => {
        await API.updateCategory(c.category_id, { active: c.active ? 0 : 1 });
        c.active = c.active ? 0 : 1;
      });
    },
    async setModelMode(c, mode) {
      await this.guard(async () => {
        await API.updateCategory(c.category_id, { model_mode: mode });
        c.model_mode = mode;
      });
    },
    async deleteCategory(c) {
      if (!await PosConfirm.ask(`確定刪除商品種類「${c.name}」?刪除後無法復原。`,
                                { danger: true })) return;
      await this.guard(async () => {
        await API.deleteCategory(c.category_id);
        if (this.selCatId === c.category_id) this.selCatId = null;
        await this.reloadAll();
      });
    },

    // ==== 商品設定:規格模板 ====
    isFeature(f) { return f.field_type === "tags"; },
    isModelRow(f) { return f.field_id === MODEL_ROW_ID; },
    // 尚未啟用特性詞條的佔位列(還沒有真的 AttributeField)
    isFeaturePlaceholder(f) { return f.field_id === FEATURE_ROW_ID; },
    // 點列:型號列切換使用與否,其餘照原本開規格編輯
    toggleModelMode() {
      const cat = this.selectedCat;
      if (!cat) return;
      this.setModelMode(cat, cat.model_mode === "required" ? "hidden" : "required");
    },
    // 特性詞條開關:沒有就替本種類建一份(每個種類各自一份,選項不互通),
    // 已有就照一般規格欄的移除語意處理(先問影響筆數)。
    // 固定列的開關:手機型號改 model_mode,特性詞條建立/移除本種類那一份
    toggleFixedRow(f) {
      if (this.isModelRow(f)) return this.toggleModelMode();
      return this.toggleFeatureField(f);
    },
    async toggleFeatureField(f) {
      if (this.selCatId == null) return;
      if (this.isFeaturePlaceholder(f)) {
        await this.guard(async () => {
          await API.createField({ name: "特性詞條", category_id: this.selCatId,
                                  field_type: "tags" });
          await this.loadCategoryDetail();
        });
        return;
      }
      await this.deleteTemplateField(f);
    },
    // 紅色 ✕:把規格欄從此種類移除,並清掉此種類商品填過的值(欄位本身若沒人再用才一起刪)
    async deleteTemplateField(f) {
      const used = f.cat_usage_count || 0;
      const feature = this.isFeature(f);
      const title = feature ? "停用特性詞條?" : `刪除規格「${f.name}」?`;
      const subject = feature ? "詞條" : `「${f.name}」`;
      const action = feature ? "停用" : "刪除";
      const impact = used
        ? `此規格有 ${used} 筆商品已填${subject}，${action}後將清除且無法復原。`
        : `此規格目前沒有商品填過${subject}。`;
      if (!await PosConfirm.ask(impact, { title, danger: true })) return;
      await this.guard(async () => {
        await API.deleteCategoryField(this.selCatId, f.field_id);
        await this.loadCategoryDetail();
      });
    },
    // 列上控制項即時存檔:成功或失敗都重讀本種類模板,
    // 失敗時下拉／勾選／開關才不會停在沒存進去的狀態
    async catGuard(fn) {
      try { await fn(); }
      catch (e) { this.showError(e.message); }
      finally { await this.guard(() => this.loadCategoryDetail()); }
    },
    // 只有下拉／複選才有選項可維護,文字欄不開選項視窗
    hasOptions(f) { return f.field_type === "select" || f.field_type === "multi"; },
    // 自訂規格新增:名稱＋型態直接在清單上方新增,排序接在最後
    async addTemplateField() {
      this.errorScope = "category";
      const name = (this.newField.name || "").trim();
      if (!name) { this.showError("請輸入規格名稱"); return; }
      if (this.selCatId == null) return;
      await this.guard(async () => {
        await API.createField({ name, category_id: this.selCatId,
                                field_type: this.newField.field_type });
        this.newField = { name: "", field_type: "select" };
        await this.loadCategoryDetail();
      });
    },
    // 列上改名:欄位為全域共用,改名會同步套用到所有使用此欄的種類
    async saveFieldName(f) {
      const name = (f.name || "").trim();
      const current = (this._fieldNameSnap || {})[f.field_id];
      if (!name) { this.showError("規格名稱不可空白"); await this.loadCategoryDetail(); return; }
      if (name === current) return;
      await this.catGuard(() => API.updateField(f.field_id, { name }));
    },
    async setFieldType(f, fieldType) {
      if (fieldType === f.field_type) return;
      await this.catGuard(() => API.updateField(f.field_id, { field_type: fieldType }));
    },
    // 必填切換只往前生效:既有子產品不停用、不強制補值,缺值者列入待補清單
    async setFieldRequired(f, required) {
      await this.catGuard(() => API.setCategoryField(
        this.selCatId, f.field_id, { required: required ? 1 : 0 }));
    },
    async setFieldActive(f, active) {
      await this.catGuard(() => API.setCategoryField(
        this.selCatId, f.field_id, { active: active ? 1 : 0 }));
    },
    // 拖拉排序儲存:CategoryField.sort 自 1 起逐欄寫回(固定列不參與)
    async saveFieldSort(ids) {
      await this.catGuard(async () => {
        let sort = 1;
        for (const fid of ids)
          await API.setCategoryField(this.selCatId, fid, { sort: sort++ });
      });
    },
    // 選項維護開 pywebview 子視窗(可拖、可縮):名稱／型態／必填／啟用／排序都在列上,
    // 視窗只負責選項清單與建檔預設帶入值。開窗前鎖主視窗,開窗失敗自己解鎖。
    async openFieldPopup(f) {
      if (!f || !this.hasOptions(f)) return;   // 文字欄沒有選項可維護
      if (this.selCatId == null) return;
      window.PosDesktopLock.lock();
      try {
        await API.invoke("desktop.child_window.open", {
          page: "field_editor",
          title: "規格選項",
          context: {
            category_id: this.selCatId,
            field_id: f.field_id,
          },
        });
      } catch (error) {
        window.PosDesktopLock.unlock();
        this.showError(error.message);
      }
    },
    async onChildWindowClosed(event) {
      const saved = !!(event && event.detail && event.detail.saved);
      if (!saved || this.selCatId == null) return;
      await this.guard(() => this.loadCategoryDetail());
    },

    // ==== 商品設定:產品 ====
    openProductPopup(p) {
      if (p) {
        this.prodPopup = {
          mode: "edit", product_id: p.product_id, name: p.name, nameDirty: true,
          brandQuery: p.brand_name || "", brand_id: p.brand_id, brand_name: null,
          note: p.note || "", active: !!p.active,
        };
      } else {
        this.prodPopup = {
          mode: "new", product_id: null, name: "", nameDirty: false,
          brandQuery: "", brand_id: null, brand_name: null, note: "", active: true,
        };
      }
    },
    onProdNameInput() { if (this.prodPopup) this.prodPopup.nameDirty = true; },
    // 自動命名:廠牌＋種類(中間一個空格,與既有資料寫法一致)。
    // 使用者一動手打過名稱(nameDirty)就不再接管,改用「重新自動命名」還原。
    autoName() {
      const p = this.prodPopup;
      const brand = p ? p.brandQuery.trim() : "";
      const cat = this.selectedCat ? this.selectedCat.name : "";
      return (brand && cat) ? brand + " " + cat : "";
    },
    refreshAutoName() {
      const p = this.prodPopup;
      if (!p || p.nameDirty) return;
      const auto = this.autoName();
      if (auto) p.name = auto;
    },
    restoreAutoName() {
      const p = this.prodPopup;
      if (!p) return;
      p.nameDirty = false;
      this.refreshAutoName();
    },
    // 從候選挑一個廠牌:記住 id,名稱同步進輸入框
    pickBrand(b) {
      const p = this.prodPopup;
      p.brand_id = b.brand_id; p.brand_name = null; p.brandQuery = b.name;
      this.refreshAutoName();
    },
    // 清單裡沒有的廠牌:存檔時一併建立
    addInlineBrand(name) {
      const p = this.prodPopup;
      p.brand_id = null; p.brand_name = name; p.brandQuery = name;
      this.refreshAutoName();
    },
    // 手打時解除既有對應,等於「還沒選定哪一個廠牌」
    onBrandQueryInput(value) {
      const p = this.prodPopup;
      p.brandQuery = value;
      p.brand_id = null; p.brand_name = null;
      this.refreshAutoName();
    },
    _brandPayload(p) {
      if (p.brand_id != null) return { brand_id: p.brand_id };
      const q = p.brandQuery.trim();
      if (q) {
        const hit = this.brands.find(b => (b.name || "").trim().toLowerCase() === q.toLowerCase());
        return hit ? { brand_id: hit.brand_id } : { brand_name: q };
      }
      return {};
    },
    async saveProduct() {
      const p = this.prodPopup;
      const name = (p.name || "").trim();
      if (!name) { this.showError("請輸入產品名稱"); return; }
      const brand = this._brandPayload(p);
      await this.guard(async () => {
        if (p.mode === "new") {
          await API.createProduct(Object.assign(
            { name, category_id: this.selCatId, note: p.note.trim() || null }, brand));
        } else {
          await API.updateProduct(p.product_id, Object.assign(
            { name, note: p.note.trim() || null, active: p.active ? 1 : 0 }, brand));
        }
        this.prodPopup = null;
        await this.loadCategoryDetail();
      });
    },
    async toggleProductActive(p) {
      await this.guard(async () => {
        await API.updateProduct(p.product_id, { active: p.active ? 0 : 1 });
        await this.loadCategoryDetail();
      });
    },
    async deleteProduct(p) {
      if (!await PosConfirm.ask(`確定刪除產品「${p.name}」?刪除後無法復原。`,
                                { danger: true })) return;
      await this.guard(async () => {
        await API.deleteProduct(p.product_id);
        await this.loadCategoryDetail();
      });
    },

    // ==== 通用清單維護(廠牌/手機品牌/型號)====
    _itemBody(kind, item) {
      const body = { name: (item.name || "").trim() };
      if (kind === "models") {
        body.alias = (item.alias || "").trim() || null;
        body.series = (item.series || "").trim() || null;
      }
      return body;
    },
    _takeSnap() {
      this._snap = {};
      this.snap = this._snap;
      for (const kind of Object.keys(_MAINT)) {
        const m = _MAINT[kind];
        for (const it of (this[kind] || []))
          this._snap[kind + ":" + it[m.id]] = JSON.stringify(this._itemBody(kind, it));
      this.snap = Object.assign({}, this._snap);
      }
    },
    // 單筆即時儲存名稱:離開欄位或按 Enter 就寫入,沒改動不送。
    async saveItemName(kind, item) {
      this.errorScope = kind;
      const m = _MAINT[kind];
      const body = this._itemBody(kind, item);
      if (!body.name) { this.showError("名稱不可空白"); await this.reloadAll(); return; }
      if (this.snap[kind + ":" + item[m.id]] === JSON.stringify(body)) return;
      await this.guardReload(() => API[m.update](item[m.id], body));
    },
    // 尚未儲存的名稱修改筆數(提醒條用)
    dirtyCount(kind) {
      const m = _MAINT[kind];
      return (this[kind] || []).filter(
        it => this.snap[kind + ":" + it[m.id]] !== JSON.stringify(this._itemBody(kind, it))
      ).length;
    },
    // 區塊的儲存:名稱修改與拖過的順序一起送出
    async saveAll(kind) {
      this.errorScope = kind;
      const m = _MAINT[kind];
      await this.guard(async () => {
        for (const it of (this[kind] || [])) {
          const body = this._itemBody(kind, it);
          if (!body.name) { this.showError("名稱不可空白"); return; }
          if (this._snap[kind + ":" + it[m.id]] === JSON.stringify(body)) continue;
          await API[m.update](it[m.id], body);
        }
        if (this.pendingSort[kind]) {
          await API[m.sort](this.pendingSort[kind]);
          delete this.pendingSort[kind];
        }
        if (kind === "models") {
          for (const ids of Object.values(this.pendingModelSort)) await API[m.sort](ids);
          this.pendingModelSort = {};
        }
        await this.reloadAll();
      });
    },
    async toggleActive(kind, item) {
      this.errorScope = kind;
      const m = _MAINT[kind];
      await this.guard(async () => {
        await API[m.update](item[m.id], { active: item.active ? 0 : 1 });
        item.active = item.active ? 0 : 1;
      });
    },
    // 重新載入會拿回資料庫的內容,把使用者還沒儲存的名稱修改與拖過的順序蓋掉。
    // 這裡先把未儲存的部分收起來,動作做完再貼回去(被刪掉的項目自然貼不回去,
    // 其餘一筆都不會消失)。
    _collectEdits(kind) {
      const m = _MAINT[kind];
      const edits = {};
      for (const it of (this[kind] || [])) {
        const body = this._itemBody(kind, it);
        if (this.snap[kind + ":" + it[m.id]] !== JSON.stringify(body))
          edits[it[m.id]] = body;
      }
      return edits;
    },
    _restoreEdits(kind, edits) {
      const m = _MAINT[kind];
      for (const it of (this[kind] || [])) {
        const body = edits[it[m.id]];
        if (!body) continue;
        for (const key of Object.keys(body)) it[key] = body[key];
      }
      // 拖過但沒儲存的順序:剔除已不存在的 id(剛被刪掉的那筆)
      const alive = new Set((this[kind] || []).map(it => it[m.id]));
      if (this.pendingSort[kind])
        this.pendingSort[kind] = this.pendingSort[kind].filter(id => alive.has(id));
      if (kind === "models")
        for (const brand of Object.keys(this.pendingModelSort))
          this.pendingModelSort[brand] =
            this.pendingModelSort[brand].filter(id => alive.has(id));
    },
    // 包住「會重新載入」的動作:動作前收起未儲存的修改,動作後貼回去
    async keepEdits(kind, operation) {
      const edits = this._collectEdits(kind);
      await operation();
      this._restoreEdits(kind, edits);
    },
    async deleteItem(kind, item) {
      this.errorScope = kind;
      const m = _MAINT[kind];
      if (!await PosConfirm.ask(`確定刪除${m.label}「${item.name}」?刪除後無法復原。`,
                                { danger: true })) return;
      await this.keepEdits(kind, () => this.guard(async () => {
        await API[m.delete](item[m.id]);
        if (this.openBrand === item[m.id]) this.openBrand = null;
        await this.reloadAll();
      }));
    },
    async saveSort(kind, ids) {
      this.errorScope = kind;
      const m = _MAINT[kind];
      await this.guardReload(() => API[m.sort](ids));
    },
    // 拖過但還沒按儲存的順序:先記著,和名稱修改一起送出
    onSortPending(kind, ids) { this.pendingSort[kind] = ids; },
    // 型號分品牌記錄(搜尋中排序已停用,收到的一定是該品牌的完整順序)
    onModelSortPending(brand, ids) { this.pendingModelSort[brand] = ids; },
    // 提醒條:名稱改過或順序拖過都算未儲存
    hasUnsaved(kind) {
      if (kind === "models" && Object.keys(this.pendingModelSort).length) return true;
      return !!this.pendingSort[kind] || this.dirtyCount(kind) > 0;
    },
    // 搜尋中一律展開(要看到結果);沒搜尋才看手動收合的狀態
    isBrandOpen(brand) {
      if (this.modelQuery.trim()) return true;
      return !this.collapsedBrands[brand];
    },
    toggleBrand(brand) {
      if (this.modelQuery.trim()) return;   // 搜尋結果不收合
      this.collapsedBrands[brand] = !this.collapsedBrands[brand];
    },
    // 復原:丟掉未儲存的名稱修改與拖過的順序,重新讀回資料庫的內容
    async revertAll(kind) {
      this.errorScope = kind;
      delete this.pendingSort[kind];
      if (kind === "models") this.pendingModelSort = {};
      await this.guard(() => this.reloadAll());
    },
    async _applyNewSeq(kind, list, newId) {
      const t = (this.newSeq[kind] || "").trim();
      this.newSeq[kind] = "";
      if (!/^[0-9]+$/.test(t) || !newId) return;
      const m = _MAINT[kind];
      const ids = list.map(x => x[m.id]).filter(x => x !== newId);
      const pos = Math.min(Math.max(parseInt(t, 10), 1), ids.length + 1);
      ids.splice(pos - 1, 0, newId);
      await API[m.sort](ids);
      await this.reloadAll();
    },
    async addItem(kind) {
      this.errorScope = kind;
      const m = _MAINT[kind];
      const name = (this.newItem[kind] || "").trim();
      if (!name) { this.showError(`請輸入${m.label}名稱`, kind); return; }
      await this.keepEdits(kind, () => this.guard(async () => {
        const r = await API[m.create]({ name });
        this.newItem[kind] = "";
        await this.reloadAll();
        await this._applyNewSeq(kind, this[kind], r[m.id]);
      }));
    },
    async addModel() {
      this.errorScope = "models";
      const pbid = this.newModel.phone_brand_id, name = this.newModel.name.trim();
      if (!pbid || !name) { this.showError("請選擇手機品牌並輸入型號名稱"); return; }
      const series = (this.newModel.series || "").trim() || null;
      await this.keepEdits("models", () => this.guard(async () => {
        const r = await API.createModel({ phone_brand_id: pbid, name, series });
        this.newModel = { phone_brand_id: null, name: "", series: "" };
        await this.reloadAll();
        const grp = this.models.filter(m => m.phone_brand_id === pbid);
        await this._applyNewSeq("models", grp, r.model_id);
      }));
    },

    // ==== 廠牌經營種類 ====
    async openBrandEditor(b) {
      if (this.openBrand === b.brand_id) { this.openBrand = null; return; }
      this.openBrand = b.brand_id; this.openBrandName = b.name;
      const checked = {};
      await this.guard(async () => {
        for (const c of this.categories) {
          const list = await API.listBrands({ category_id: c.category_id });
          if (list.some(x => x.brand_id === b.brand_id)) checked[c.category_id] = true;
        }
        this.brandCatChecked = checked;
      });
    },
    async toggleBrandCat(c) {
      const checked = Object.assign({}, this.brandCatChecked);
      checked[c.category_id] = !checked[c.category_id];
      const ids = this.categories.filter(x => checked[x.category_id]).map(x => x.category_id);
      await this.guard(async () => {
        await API.setBrandCategories(this.openBrand, ids);
        this.brandCatChecked = checked;
      });
    },
  },
};
