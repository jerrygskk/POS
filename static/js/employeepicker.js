window.PosComponents = window.PosComponents || {};
// 操作員工代號 1~6:收銀／進貨／盤點共用同一個選擇,切頁與結帳後都保留,
// 只活在本次開啟程式期間(同 PosCheckoutDraft)。
window.PosEmployee = Vue.reactive({ id: null });
// 選用的綁定值(v-model):有給就只改那個值,不動全店共用的選擇(單據更正框用);沒給維持共用。
window.PosComponents["employee-picker"] = {
  props: { modelValue: { default: undefined } },
  emits: ["update:modelValue"],
  data() { return { state: window.PosEmployee, ids: [1, 2, 3, 4, 5, 6] }; },
  computed: {
    current() { return this.modelValue !== undefined ? this.modelValue : this.state.id; },
  },
  methods: {
    pick(n) {
      if (this.modelValue !== undefined) this.$emit("update:modelValue", n);
      else this.state.id = n;
    },
  },
  template: `<span class="employee-picker" title="操作員工代號">
    <button v-for="n in ids" :key="n" type="button" class="chip-toggle"
            :class="{on: current === n}" @click="pick(n)">{{ n }}</button></span>`,
};
