window.PosComponents = window.PosComponents || {};

// 共用型號選取元件:外觀比照規格值的候選選取器(opt-picker)——已選區在最上、
// 搜尋框、候選 chip 點一下切換。chip 內不再放勾選方塊、廠牌也不再預設收合:
// 同一個畫面上兩種複選長得不一樣,店員得學兩套操作。
// ⚠️ 搜尋框放在候選區「上面」(opt-picker 是放下面):型號候選全展開很長,
// 放下面會被推到看不見的地方。
// 前排(lead):這個產品／廠牌真的賣過的機型,其餘收進「更多…」——型號上百個,
// 全部攤開太擠。資料來自 `variants.model_usage`;沒給 usage 就全部展開。
// 裝置類型(手機／平板…)超過一種時,上方出現類型 chip 可只看某一類;
// 品牌底下依類型分小節(服務層已依品牌→類型→型號排序)。
// props:models(全部型號,含 brand_name/device_type_id/device_type_name/alias)、usage(model_usage 回傳)、
//       model_ids(v-model 陣列)。
// 勾選以 emit 新陣列方式更新(不就地改 prop),進出格式仍是 int 陣列。
window.PosComponents["model-picker"] = {
  props: {
    models: { type: Array, required: true },
    usage: { type: Array, default: () => [] },
    model_ids: { type: Array, required: true },
  },
  emits: ["update:model_ids"],
  data() { return { query: "", showMore: false, typeFilter: null }; },
  computed: {
    // 已選型號(依全域型號排序),點擊可取消
    selectedModels() {
      return this.models.filter(m => this.model_ids.includes(m.model_id));
    },
    // 前排型號 id;沒有 usage 或這個範圍沒賣過任何機型時為空(等於全部展開)
    leadIds() {
      return new Set((this.usage || []).filter(u => u.lead).map(u => u.model_id));
    },
    // 出現過的裝置類型(依型號清單順序);只有一種時不顯示篩選列
    deviceTypes() {
      const seen = new Map();
      for (const m of this.models)
        if (!seen.has(m.device_type_id)) seen.set(m.device_type_id, m.device_type_name);
      return [...seen].map(([id, name]) => ({ id, name }));
    },
    // 搜尋比對名稱、別名、裝置類型與品牌;沒打字就是全部。類型篩選疊在搜尋之上
    matchedModels() {
      const q = this.query.trim().toLowerCase();
      return this.models.filter(m =>
        (this.typeFilter == null || m.device_type_id === this.typeFilter)
        && (!q || [m.name, m.alias, m.device_type_name, m.brand_name]
          .some(text => String(text || "").toLowerCase().includes(q))));
    },
    // 收合狀態下只顯示前排;搜尋中一律搜全部(找舊機型時不必先按更多…)
    visibleModels() {
      if (this.query.trim() || this.showMore || !this.leadIds.size) return this.matchedModels;
      return this.matchedModels.filter(m => this.leadIds.has(m.model_id)
        || this.model_ids.includes(m.model_id));
    },
    hiddenCount() {
      return this.matchedModels.length - this.visibleModels.length;
    },
    // [{ brand, sections: [{ type, items:[...] }] }];品牌內只有一種類型時不印小節標題
    filteredGroups() {
      const groups = [];
      for (const m of this.visibleModels) {
        let group = groups[groups.length - 1];
        if (!group || group.brand !== m.brand_name) {
          group = {brand: m.brand_name, sections: []};
          groups.push(group);
        }
        let section = group.sections[group.sections.length - 1];
        if (!section || section._key !== m.device_type_id) {
          section = {_key: m.device_type_id, type: m.device_type_name, items: []};
          group.sections.push(section);
        }
        section.items.push(m);
      }
      for (const group of groups)
        if (group.sections.length < 2) for (const section of group.sections) section.type = "";
      return groups;
    },
  },
  methods: {
    isOn(mid) { return this.model_ids.includes(mid); },
    toggle(mid) {
      const arr = this.model_ids.slice();
      const i = arr.indexOf(mid);
      if (i >= 0) arr.splice(i, 1); else arr.push(mid);
      this.$emit("update:model_ids", arr);
    },
    brandSelectedCount(brand) {
      return this.models.filter(m => m.brand_name === brand
        && this.model_ids.includes(m.model_id)).length;
    },
  },
  template: `
  <div class="tag-selector model-selector">
    <div class="chip-wrap tag-picked mp-selected">
      <span v-for="m in selectedModels" :key="'sel-' + m.model_id" class="chip on tag-chip">
        {{ m.alias || m.name }}
        <button type="button" class="tag-x" title="取消選擇"
                @click="toggle(m.model_id)">✕</button>
      </span>
      <span v-if="!selectedModels.length" class="tag-empty">尚未選擇型號</span>
    </div>
    <div class="tag-search">
      <input v-model="query" placeholder="搜尋型號">
    </div>
    <div v-if="deviceTypes.length > 1" class="chip-wrap mp-types">
      <button type="button" class="chip" :class="{ on: typeFilter == null }"
              @click="typeFilter = null">全部</button>
      <button type="button" v-for="t in deviceTypes" :key="t.id" class="chip"
              :class="{ on: typeFilter === t.id }"
              @click="typeFilter = t.id">{{ t.name }}</button>
    </div>
    <div class="model-picker">
      <template v-for="g in filteredGroups" :key="g.brand">
        <div class="mp-brand">{{ g.brand }}<template v-if="brandSelectedCount(g.brand) > 0">（已選 {{ brandSelectedCount(g.brand) }}）</template></div>
        <template v-for="(sec, si) in g.sections" :key="si">
          <div v-if="sec.type" class="mp-type">{{ sec.type }}</div>
          <div class="chip-wrap mp-items">
            <button type="button" v-for="m in sec.items" :key="m.model_id" class="chip"
                    :class="{ on: isOn(m.model_id) }"
                    @click="toggle(m.model_id)">{{ m.alias || m.name }}</button>
          </div>
        </template>
      </template>
      <div v-if="!filteredGroups.length" class="tag-empty">查無型號</div>
      <button type="button" v-if="hiddenCount > 0" class="chip tag-more"
              @click="showMore=true">更多…（{{ hiddenCount }}）</button>
    </div>
  </div>`,
};
