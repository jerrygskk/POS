window.PosComponents = window.PosComponents || {};

// 共用規格欄元件:依 fields 逐欄渲染 multi/tags/select/text 分支(進貨④規格、
// 資料庫變體編輯、資料庫新增子產品三處共用)。
// props:
//   fields    規格欄定義陣列
//   options   select/multi 選項來源(field_id → 選項清單,即各頁 fieldOptions)
//   attrs     屬性值物件(就地綁 v-model,multi=陣列、tags/select/text=字串)
//   modelIds  目前適用型號(過濾 select/multi 選項用)
//   tagsStyle tags 呈現:'datalist'(輸入+建議,預設)或 'chips'(詞條鈕切換)
//   showEmptyHint multi 無選項時是否顯示「尚無選項」(進貨建檔用)
// datalist id 由實例序號自動產生,消除各頁 dl-/edl-/ndl- 前綴差異且避免同頁碰撞。
let _afSeq = 0;
window.PosComponents["attr-fields"] = {
  props: {
    fields: { type: Array, required: true },
    options: { type: Object, default: () => ({}) },
    attrs: { type: Object, required: true },
    modelIds: { type: Array, default: () => [] },
    tagsStyle: { type: String, default: "datalist" },
    showEmptyHint: { type: Boolean, default: false },
  },
  data() { return { afUid: ++_afSeq }; },
  methods: {
    optionsFor(f) {
      return window.CatalogFields.filterOptions(this.options[f.field_id] || [], this.modelIds);
    },
    datalistId(f) { return "af-" + this.afUid + "-" + f.field_id; },
    tagList(str) {
      return String(str || "").split(/[,、，]/).map(s => s.trim()).filter(Boolean);
    },
    tagHas(str, val) { return this.tagList(str).includes(val); },
    toggleTag(obj, fname, val) {
      const arr = this.tagList(obj[fname]);
      const i = arr.indexOf(val);
      if (i >= 0) arr.splice(i, 1); else arr.push(val);
      obj[fname] = arr.join(", ");
    },
  },
  template: `
  <div v-for="f in fields" :key="f.field_id" class="attr-row">
    <template v-if="f.field_type === 'multi'">
      <div class="attr-name">{{ f.name }}</div>
      <div class="chip-box">
        <div class="chip-wrap">
          <label v-for="o in optionsFor(f)" :key="o.option_id" class="chip"
                 :class="{ on: (attrs[f.name] || []).includes(o.value) }">
            <input type="checkbox" :value="o.value" v-model="attrs[f.name]">
            {{ o.value }}
          </label>
          <span v-if="showEmptyHint && !optionsFor(f).length" class="hint">尚無選項</span>
        </div>
      </div>
    </template>
    <template v-else-if="f.field_type === 'tags' && tagsStyle === 'chips'">
      <div class="attr-name">{{ f.name }}</div>
      <div class="chip-box">
        <div class="chip-wrap">
          <button type="button" v-for="o in (f.options || [])" :key="o.option_id" class="chip"
                  :class="{ on: tagHas(attrs[f.name], o.value) }"
                  @click="toggleTag(attrs, f.name, o.value)">{{ o.value }}</button>
        </div>
        <input v-model="attrs[f.name]" placeholder="以逗號分隔,可自由新增">
      </div>
    </template>
    <label v-else-if="f.field_type === 'tags'">{{ f.name }}
      <input v-model="attrs[f.name]" :list="datalistId(f)"
             placeholder="以逗號分隔,可自由新增">
      <datalist :id="datalistId(f)">
        <option v-for="o in (f.options || [])" :key="o.option_id" :value="o.value"></option>
      </datalist>
    </label>
    <label v-else-if="f.field_type === 'select'">{{ f.name }}
      <input :list="datalistId(f)" v-model="attrs[f.name]">
      <datalist :id="datalistId(f)">
        <option v-for="o in optionsFor(f)" :key="o.option_id" :value="o.value"></option>
      </datalist>
    </label>
    <label v-else>{{ f.name }}
      <input v-model="attrs[f.name]">
    </label>
  </div>`,
};
