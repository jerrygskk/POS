window.PosComponents = window.PosComponents || {};

// 共用規格欄元件:依 fields 逐欄渲染 multi/tags/select/text 分支
// (修改款式、新增款式建檔頁、建檔頁的修改 draft 三處共用)。
// props:
//   fields    規格欄定義陣列
//   attrs     屬性值物件(就地綁 v-model,multi=陣列、tags/select/text=字串)
//   modelIds  目前適用型號(過濾候選用)
//   usage     field_id → 使用次數排序候選
// 規格值一律走候選選取器(opt-picker):該範圍用過的值排前面、可搜尋、可直接新增。
// 原本還有一條「候選沒載入就退回原生 datalist／勾選框」的退路,已移除——
// 那條退路會靜默換成外觀完全不同的介面,出事也看不出來。
window.PosComponents["attr-fields"] = {
  props: {
    fields: { type: Array, required: true },
    attrs: { type: Object, required: true },
    modelIds: { type: Array, default: () => [] },
    // usage: field_id → 該種類使用次數排序候選(API.fieldUsage);
    // select/multi/tags 一律走候選選取器,候選還沒載入就先顯示載入中。
    usage: { type: Object, default: () => ({}) },
  },
  template: `
  <div v-for="f in fields" :key="f.field_id" class="attr-row">
    <template v-if="f.field_type === 'multi'">
      <div class="attr-name">{{ f.name }}</div>
      <div class="chip-box">
        <opt-picker v-if="usage[f.field_id]" :model-value="attrs[f.name]"
                    @update:model-value="attrs[f.name] = $event"
                    :usage="usage[f.field_id]" :multiple="true" :as-list="true"
                    :model-ids="modelIds" :placeholder="'搜尋或輸入' + f.name"></opt-picker>
        <span v-else class="hint">候選載入中…</span>
      </div>
    </template>
    <template v-else-if="f.field_type === 'tags'">
      <div class="attr-name">{{ f.name }}</div>
      <div class="chip-box">
        <opt-picker :model-value="attrs[f.name]"
                    @update:model-value="attrs[f.name] = $event"
                    :usage="usage[f.field_id] || []" :multiple="true" :as-list="false"
                    :model-ids="modelIds" :placeholder="'搜尋或輸入' + f.name"></opt-picker>
      </div>
    </template>
    <template v-else-if="f.field_type === 'select'">
      <div class="attr-name">{{ f.name }}</div>
      <div class="chip-box">
        <opt-picker v-if="usage[f.field_id]" :model-value="attrs[f.name]"
                    @update:model-value="attrs[f.name] = $event"
                    :usage="usage[f.field_id]" :multiple="false" :model-ids="modelIds"
                    :placeholder="'搜尋或輸入' + f.name"></opt-picker>
        <span v-else class="hint">候選載入中…</span>
      </div>
    </template>
    <label v-else>{{ f.name }}
      <input v-model="attrs[f.name]">
    </label>
  </div>`,
};
