window.PosComponents = window.PosComponents || {};

// 拖拉排序清單:⠿ 把手整列拖、序號格打數字+Enter 搬位。
// autoSave=true 拖完直接 emit save(ids);否則只 emit pending(ids),
// 交給該區塊既有的儲存按鈕一起送(清單自己不放儲存／取消按鈕)。
window.PosComponents["sortable-list"] = {
  // autoSave:拖完立刻寫入(名稱也即時存的清單用);false 則只回報新順序,
  // 由該區塊的「儲存名稱修改」一起送出——清單自己不再長出儲存／取消按鈕。
  props: { items: { type: Array, required: true },
           itemKey: { type: String, required: true },
           activeKey: { type: String, default: "active" },
           autoSave: { type: Boolean, default: false },
           // disabled:清單被過濾時要關掉排序——畫面上只剩部分項目,
           // 送出的順序會把沒顯示的那些擠掉(排序 API 是依序重編 1..N)。
           disabled: { type: Boolean, default: false } },
  emits: ["save", "pending"],
  data() { return { rows: [], dirty: false, dragIdx: null, overIdx: null }; },
  watch: {
    items: { immediate: true,
      handler(v) { this.rows = (v || []).slice(); this.dirty = false; } },
  },
  methods: {
    moveRow(src, dst) {
      if (this.disabled || src === dst || src < 0 || dst < 0) return;
      this.rows.splice(dst, 0, this.rows.splice(src, 1)[0]);
      this.dirty = true;
      const ids = this.rows.map(r => r[this.itemKey]);
      if (this.autoSave) { this.$emit("save", ids); this.dirty = false; }
      else this.$emit("pending", ids);
    },
    onDragStart(i, ev) {
      if (this.disabled) { ev.preventDefault(); return; }
      this.dragIdx = i;
      ev.dataTransfer.effectAllowed = "move";
      // 拖影用整列,不然只有把手小字
      const row = ev.target.closest(".maint-row");
      if (row) ev.dataTransfer.setDragImage(row, 20, 20);
    },
    onDragOver(i, ev) {
      if (this.disabled) return;
      // 游標在列上半=插到本列前(insertPos=i),下半=插到本列後(i+1)
      const r = ev.currentTarget.getBoundingClientRect();
      this.overIdx = ev.clientY < r.top + r.height / 2 ? i : i + 1;
    },
    onDrop() {
      if (this.dragIdx !== null && this.overIdx !== null) {
        // 先移除 src 再插入,src 在插入點之前時目標索引要 -1
        const dst = this.overIdx > this.dragIdx ? this.overIdx - 1 : this.overIdx;
        this.moveRow(this.dragIdx, dst);
      }
      this.dragIdx = null; this.overIdx = null;
    },
    onSeqCommit(i, ev) {
      const t = (ev.target.value || "").trim();
      const n = /^[0-9]+$/.test(t) ? parseInt(t, 10) : null;
      if (n !== null && 1 <= n && n <= this.rows.length && n - 1 !== i)
        this.moveRow(i, n - 1);
      else
        ev.target.value = String(i + 1);   // 不合法安靜跳回
      ev.target.blur();
    },
    onSeqBlur(i, ev) { ev.target.value = String(i + 1); },  // 離焦還原顯示
  },
  template: `
  <div>
    <template v-for="(it, i) in rows" :key="it[itemKey]">
      <div class="maint-row"
           :class="{ inactive: activeKey in it && !it[activeKey], 'drop-before': overIdx === i,
                     'drop-after': overIdx === i + 1 && i === rows.length - 1 }"
           @dragover.prevent="onDragOver(i, $event)"
           @dragleave="(overIdx === i || overIdx === i + 1) && (overIdx = null)"
           @drop.prevent="onDrop()">
        <span class="drag-handle" :class="{ disabled }" :draggable="!disabled"
              :title="disabled ? '搜尋中不可調整順序' : '按住拖拉調整排序'"
              @dragstart="onDragStart(i, $event)" @dragend="dragIdx = null; overIdx = null">⠿</span>
        <input class="seq-cell" :value="i + 1" :disabled="disabled"
               :title="disabled ? '搜尋中不可調整順序' : '輸入序號後按 Enter 可搬移'"
               @keyup.enter="onSeqCommit(i, $event)" @blur="onSeqBlur(i, $event)">
        <slot name="row" :item="it" :index="i"></slot>
      </div>
      <slot name="detail" :item="it" :index="i"></slot>
    </template>
  </div>`,
};
