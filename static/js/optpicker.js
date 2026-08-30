window.PosComponents = window.PosComponents || {};

// 選項候選選取器(opt-picker):規格值輸入的公版。
//
// ── 什麼時候用哪一個(專案內三種輸入公版的分工) ────────────────────
//   opt-picker(本檔)  值域會長大、需要「猜常用」的欄位:規格值、特性詞條。
//                     前排排出這個範圍用過的值並標次數,其餘收進「更多…」,
//                     搜尋得到全部,打不存在的值可當場新增。需要後端使用次數。
//   combo-box         從一份既有主檔挑一筆的欄位:廠牌、供應商之類。
//                     只有搜尋與「新增○○」,不排常用、不看次數。
//   原生 <select>     選項固定且很少(型態、狀態)。不要為了統一外觀改用上面兩個。
//   ⚠️ 不要再寫第四種。原本還有一條 datalist 退路,已於改版移除——
//      它會靜默換成外觀完全不同的介面,出問題也看不出來。
//
// ── 這支元件的遊戲規則 ──────────────────────────────────────────
//  1. 前排(chip)取服務層標記 lead 的值:該廠牌 → 該產品 → 都沒有才退回種類
//     使用次數前 8。chip 上的數字對應目前採用的那一種範圍。
//  2. 選過的值移出候選,顯示在最上排、可按 ✕ 移除。
//  3. 停用中的選項仍會出現在搜尋結果,選了就標「將重新啟用」——不是隱藏,
//     店員找不到舊值只會自己再打一個同名的新選項。
//  4. 搜尋框打不存在的值時才出現「新增「○○」」;完全相符就不提示。
//  5. 適用型號會過濾候選(限定型號的特別色只在對應型號出現)。
//  6. 本元件不寫資料庫:新值一樣隨表單送出,由後端在存檔時建立。
//
// props:
//   modelValue  multiple=陣列(multi)或逗號字串(tags);single=字串(select)
//   usage       API.fieldUsage 回傳(帶 lead／lead_count／usage_count、含停用與 model_ids)
//   multiple    true=可多選(tags/multi);false=單選(select,再選即取代)
//   asList      multiple 時 modelValue 型別:true=陣列(multi)、false=逗號字串(tags)
//   modelIds    目前適用型號(依 OptionModel 過濾特別色候選)
//   placeholder 搜尋框提示字
window.PosComponents["opt-picker"] = {
  props: {
    modelValue: { default: "" },
    usage: { type: Array, default: () => [] },
    multiple: { type: Boolean, default: false },
    asList: { type: Boolean, default: false },
    modelIds: { type: Array, default: () => [] },
    placeholder: { type: String, default: "搜尋或輸入" },
  },
  emits: ["update:modelValue"],
  data() { return { query: "", showMore: false }; },
  computed: {
    selected() {
      if (this.multiple)
        return this.asList
          ? (Array.isArray(this.modelValue) ? this.modelValue.slice() : [])
          : window.parseTagList(this.modelValue);
      const s = (this.modelValue == null ? "" : String(this.modelValue)).trim();
      return s ? [s] : [];
    },
    selectedKeys() { return new Set(this.selected.map(s => s.toLowerCase())); },
    pool() {
      // 依適用型號過濾特別色候選(usage row 帶 model_ids)
      return window.CatalogFields.filterOptions(this.usage, this.modelIds);
    },
    // 已選的候選 chip 留在原位(標成已選),不從候選區抽走——抽走會讓候選少一顆、
    // 行數變動,整排欄位跟著上下跳。
    available() {
      return this.pool.filter(o => o.active || this.selectedKeys.has(o.value.toLowerCase()));
    },
    // 前排:服務層標記 lead 的值(該廠牌／該產品曾出現過,含固定次序值)全部顯示,
    // 其餘收進「更多…」。完全沒有 lead(全新產品第一筆)才退回種類次數前 8。
    leadChips() { return this.available.filter(o => o.lead); },
    topChips() {
      return this.leadChips.length ? this.leadChips : this.available.slice(0, 8);
    },
    moreChips() {
      if (!this.leadChips.length) return this.available.slice(8);
      return this.available.filter(o => !o.lead);
    },
    // chip 上的數字:有前排範圍時顯示該範圍次數,否則顯示種類次數
    countOf() {
      const useLead = this.leadChips.length > 0;
      return (o) => (useLead ? o.lead_count : o.usage_count) || "";
    },
    matches() {
      const q = this.query.trim().toLowerCase();
      if (!q) return [];
      return this.pool.filter(o => o.value.toLowerCase().includes(q)).slice(0, 12);
    },
    exactExists() {
      const q = this.query.trim().toLowerCase();
      if (!q) return true;
      return this.pool.some(o => o.value.toLowerCase() === q)
        || this.selectedKeys.has(q);
    },
  },
  methods: {
    isDisabledVal(val) {
      const o = this.pool.find(u => u.value.toLowerCase() === val.toLowerCase());
      return !!(o && !o.active);
    },
    emitList(list) {
      if (!this.multiple) { this.$emit("update:modelValue", list.length ? list[list.length - 1] : ""); return; }
      this.$emit("update:modelValue", this.asList ? list.slice() : list.join(", "));
    },
    add(val) {
      val = String(val).trim();
      if (!val) return;
      if (this.selectedKeys.has(val.toLowerCase())) return;
      this.emitList(this.multiple ? this.selected.concat([val]) : [val]);
    },
    // 候選/搜尋結果的 chip:再點一次取消選取(chip 不會消失,只換樣式)
    isSelectedVal(val) { return this.selectedKeys.has(String(val).toLowerCase()); },
    toggle(o) {
      if (this.isSelectedVal(o.value)) { this.remove(o.value); return; }
      this.add(o.value);
    },
    remove(val) {
      this.emitList(this.selected.filter(s => s.toLowerCase() !== val.toLowerCase()));
    },
    // 自訂新值:字串本身已變成選中的值,留著只會讓「新增「○○」」按鈕再出現一次
    addFromSearch() { this.add(this.query); this.query = ""; },
    handleSearchEnter(event) {
      event.preventDefault();
      event.stopPropagation();
      this.addFromSearch();
    },
    // 複選時保留搜尋字與結果清單,讓使用者接著挑同一批的下一個
    // (打「透」要選透明、透明磁吸…);單選選完就結束,清空。
    pickMatch(o) {
      this.toggle(o);
      if (!this.multiple) { this.query = ""; return; }
      // 焦點留在搜尋框:點候選會把焦點帶走,接著打字就沒反應了
      if (this.$refs.search) this.$refs.search.focus();
    },
  },
  template: `
  <div class="tag-selector">
    <div class="chip-wrap tag-picked">
      <span v-for="val in selected" :key="val" class="chip on tag-chip">
        {{ val }}
        <span v-if="isDisabledVal(val)" class="tag-reactivate">(將重新啟用)</span>
        <button type="button" class="tag-x" @click="remove(val)">✕</button>
      </span>
      <span v-if="!selected.length" class="tag-empty">尚未選擇</span>
    </div>
    <div class="chip-wrap" v-if="topChips.length || moreChips.length">
      <button type="button" v-for="o in topChips" :key="o.option_id" class="chip"
              :class="{ on: isSelectedVal(o.value) }"
              @click="toggle(o)">{{ o.value }}<span class="tag-count">{{ countOf(o) }}</span></button>
      <button type="button" v-if="moreChips.length && !showMore" class="chip tag-more"
              @click="showMore=true">更多…</button>
      <template v-if="showMore">
        <button type="button" v-for="o in moreChips" :key="o.option_id" class="chip"
                :class="{ on: isSelectedVal(o.value) }"
                @click="toggle(o)">{{ o.value }}<span class="tag-count">{{ countOf(o) }}</span></button>
      </template>
    </div>
    <div class="tag-search">
      <input ref="search" v-model="query" :placeholder="placeholder"
             @keydown.enter="handleSearchEnter">
      <button type="button" class="btn-sm" v-if="query.trim() && !exactExists"
              @click="addFromSearch">新增「{{ query.trim() }}」</button>
    </div>
    <div class="chip-wrap tag-matches">
      <button type="button" v-for="o in matches" :key="o.option_id" class="chip"
              :class="{ inactive: !o.active, on: isSelectedVal(o.value) }"
              @click="pickMatch(o)">
        {{ o.value }}<span v-if="!o.active" class="tag-reactivate">(停用，將重新啟用)</span>
      </button>
      <span v-if="!matches.length" class="tag-empty">{{ query.trim() ? '查無相符的值' : '' }}</span>
    </div>
  </div>`,
};

// 相容別名:特性詞條選取器(tags 逗號字串多選)。
window.PosComponents["tag-selector"] = {
  props: { modelValue: { type: String, default: "" }, usage: { type: Array, default: () => [] } },
  emits: ["update:modelValue"],
  template: `<opt-picker :model-value="modelValue" :usage="usage" :multiple="true" :as-list="false"
    placeholder="搜尋或輸入特性詞條" @update:model-value="$emit('update:modelValue', $event)"></opt-picker>`,
};
