window.PosComponents = window.PosComponents || {};

// 拖拉排序清單:⠿ 把手整列拖、序號格打數字+Enter 搬位;
// 改動只動記憶體,亮「儲存排序」按下才 emit save(ids)。
window.PosComponents["sortable-list"] = {
  props: { items: { type: Array, required: true },
           itemKey: { type: String, required: true } },
  emits: ["save"],
  data() { return { rows: [], dirty: false, dragIdx: null, overIdx: null }; },
  watch: {
    items: { immediate: true,
      handler(v) { this.rows = (v || []).slice(); this.dirty = false; } },
  },
  methods: {
    moveRow(src, dst) {
      if (src === dst || src < 0 || dst < 0) return;
      this.rows.splice(dst, 0, this.rows.splice(src, 1)[0]);
      this.dirty = true;
    },
    onDragStart(i, ev) {
      this.dragIdx = i;
      ev.dataTransfer.effectAllowed = "move";
      // 拖影用整列,不然只有把手小字
      const row = ev.target.closest(".maint-row");
      if (row) ev.dataTransfer.setDragImage(row, 20, 20);
    },
    onDragOver(i, ev) {
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
    save() { this.$emit("save", this.rows.map(r => r[this.itemKey])); this.dirty = false; },
    reset() { this.rows = (this.items || []).slice(); this.dirty = false; },
  },
  template: `
  <div>
    <div v-if="dirty" class="inline-add sort-actions">
      <button class="primary" @click="save">儲存排序</button>
      <button @click="reset">取消</button>
    </div>
    <template v-for="(it, i) in rows" :key="it[itemKey]">
      <div class="maint-row"
           :class="{ inactive: !it.active, 'drop-before': overIdx === i,
                     'drop-after': overIdx === i + 1 && i === rows.length - 1 }"
           @dragover.prevent="onDragOver(i, $event)"
           @dragleave="(overIdx === i || overIdx === i + 1) && (overIdx = null)"
           @drop.prevent="onDrop()">
        <span class="drag-handle" draggable="true" title="按住拖拉調整排序"
              @dragstart="onDragStart(i, $event)" @dragend="dragIdx = null; overIdx = null">⠿</span>
        <input class="seq-cell" :value="i + 1" title="輸入序號後按 Enter 可搬移"
               @keyup.enter="onSeqCommit(i, $event)" @blur="onSeqBlur(i, $event)">
        <slot name="row" :item="it" :index="i"></slot>
      </div>
      <slot name="detail" :item="it" :index="i"></slot>
    </template>
  </div>`,
};
