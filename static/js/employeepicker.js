window.PosComponents = window.PosComponents || {};
// 操作員工代號 1~6:收銀／進貨／盤點共用同一個選擇,切頁與結帳後都保留,
// 只活在本次開啟程式期間(同 PosCheckoutDraft)。
window.PosEmployee = Vue.reactive({ id: null });
window.PosComponents["employee-picker"] = {
  data() { return { state: window.PosEmployee, ids: [1, 2, 3, 4, 5, 6] }; },
  template: `<span class="employee-picker" title="操作員工代號">
    <button v-for="n in ids" :key="n" type="button" class="chip-toggle"
            :class="{on: state.id === n}" @click="state.id = n">{{ n }}</button></span>`,
};
