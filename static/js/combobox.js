// 可搜尋下拉(combobox)公版:輸入框可打字搜尋、右側箭頭展開候選、可直接新增未收錄的值。
// 設定頁「廠牌」用它;之後任何「從既有清單挑一個、也允許臨時新增」的欄位都用這支,
// 不要各自重寫。⚠️ 箭頭不可用 <button> 做——全域 button 有 min-width 80px 與 hover
// 底色,會在輸入框裡冒出一塊灰(踩過)。
//
// 用法:
//   <combo-box v-model="query" :options="brands" option-key="brand_id" option-label="name"
//              placeholder="搜尋或輸入廠牌" :allow-create="true"
//              @select="pickBrand" @create="addInlineBrand"></combo-box>
// v-model 為輸入框文字;select 傳回選到的原始項目;create 傳回輸入的新名稱。
window.PosComponents = window.PosComponents || {};
window.PosComponents["combo-box"] = {
  props: {
    modelValue: { type: String, default: "" },
    options: { type: Array, default: () => [] },
    optionKey: { type: String, required: true },
    optionLabel: { type: String, default: "name" },
    placeholder: { type: String, default: "" },
    allowCreate: { type: Boolean, default: false },
    emptyText: { type: String, default: "尚無資料" },
    // 對話框開啟時要不要把游標放進來(欄位順序的第一格才設)
    autofocus: { type: Boolean, default: false },
  },
  emits: ["update:modelValue", "select", "create"],
  // 只在點箭頭或開始打字時才展開;取得焦點不自動展開——清單會蓋住下面的欄位。
  data() { return { open: false }; },
  mounted() {
    if (this.autofocus && this.$refs.input) this.$refs.input.focus();
  },
  computed: {
    query() { return (this.modelValue || "").trim(); },
    filtered() {
      const q = this.query.toLowerCase();
      if (!q) return this.options;
      return this.options.filter(
        o => String(o[this.optionLabel] || "").toLowerCase().includes(q));
    },
    // 打的字剛好等於既有項目時不再提示新增
    exactMatch() {
      if (!this.query) return true;
      const q = this.query.toLowerCase();
      return this.options.some(
        o => String(o[this.optionLabel] || "").trim().toLowerCase() === q);
    },
  },
  methods: {
    onInput(event) {
      this.open = true;
      this.$emit("update:modelValue", event.target.value);
    },
    // 點箭頭:展開／收合,焦點留在輸入框
    toggle() {
      this.open = !this.open;
      if (this.$refs.input) this.$refs.input.focus();
    },
    // 失焦稍後才收合,否則點候選項目時清單已經消失
    onBlur() { setTimeout(() => { this.open = false; }, 120); },
    pick(option) {
      this.open = false;
      this.$emit("update:modelValue", String(option[this.optionLabel] || ""));
      this.$emit("select", option);
    },
    create() {
      if (!this.query) return;
      this.open = false;
      this.$emit("create", this.query);
    },
  },
  template: `
  <span class="combo">
    <input ref="input" :value="modelValue" :placeholder="placeholder"
           @input="onInput" @blur="onBlur">
    <span class="combo-caret" role="button" :aria-expanded="open"
          aria-label="展開清單" @mousedown.prevent="toggle"></span>
    <div v-if="open" class="combo-menu">
      <div v-for="o in filtered" :key="o[optionKey]" class="combo-item"
           @mousedown.prevent="pick(o)">{{ o[optionLabel] }}</div>
      <div v-if="allowCreate && query && !exactMatch"
           class="combo-item combo-add" @mousedown.prevent="create">
        新增「{{ query }}」</div>
      <div v-if="!filtered.length && !query" class="hint" style="padding:6px">
        {{ emptyText }}</div>
    </div>
  </span>`,
};
