// 日報表(獨立程式 daily_report/main.py 的畫面)。
// 營業額與個績唯讀、取自 POS 已立帳的資料;其餘手填,按「儲存」才寫入。
// 明細列數上限照老闆的 Excel 每日頁,PDF 格子也依此配置;畫面先給 4 列,按「＋」逐列加到上限。
const SHOWN = 4;
const LIMITS = { goods: 12, owed: 6, note: 12, expense: 12 };
// 紙本下方的明細框;當日貨款、尚欠貨款在上方各自的格子裡
const LISTS = [
  { kind: "note", title: "備註", textLabel: "雜支內容" },
  { kind: "expense", title: "開(收)支明細", textLabel: "項目（收入填負數）" },
];
const TITLES = { goods: "當日貨款", owed: "尚欠貨款", note: "備註", expense: "開(收)支明細" };
const CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩";

function bridge() {
  if (window.pywebview && window.pywebview.api) return Promise.resolve(window.pywebview.api);
  return new Promise((resolve) => window.addEventListener("pywebviewready",
    () => resolve(window.pywebview.api), { once: true }));
}

async function call(method, ...args) {
  const api = await bridge();
  const result = await api[method](...args);
  if (result && result.ok) return result.data;
  throw new Error((result && result.error) || "操作失敗");
}

function money(value) {
  return value === null || value === undefined ? "" : Number(value).toLocaleString("en-US");
}

// 比較有無修改時略過空白列:只是按「＋」多出的空列不算修改
function snapshot(form) {
  const data = { weather: form.weather, payable: form.payable };
  for (const kind in LIMITS) data[kind] = form[kind].filter(line => line.text.trim() || String(line.amount).trim());
  return JSON.stringify(data);
}

// 空白＝null;可含千分位逗號;不是整數回傳 NaN 讓畫面擋下
function parseAmount(text) {
  const value = String(text ?? "").trim().replace(/,/g, "");
  if (!value) return null;
  return /^-?\d+$/.test(value) ? Number(value) : NaN;
}

function blankForm() {
  const form = { weather: "", payable: "" };
  for (const kind in LIMITS) form[kind] = Array.from({ length: SHOWN }, () => ({ text: "", amount: "" }));
  return form;
}

function formFrom(entry) {
  const form = blankForm();
  form.weather = entry.weather;
  form.payable = entry.payable === null ? "" : money(entry.payable);
  for (const kind in LIMITS)
    entry[kind].forEach((line, i) => {
      form[kind][i] = { text: line.text, amount: line.amount === null ? "" : money(line.amount) };
    });
  return form;
}

window.DailyReportApp = {
  data() {
    return {
      lists: LISTS, circled: CIRCLED,
      date: "", shownDate: "", dbPath: "", posError: "", error: "",
      row: null, form: blankForm(), saved: "", busy: false,
    };
  },
  async mounted() {
    window.__dailyReport = this;  // 走查腳本用來讀取畫面狀態
    try {
      this.date = await call("today");
      await this.load(this.date);
    } catch (error) {
      this.error = error.message;
    }
    this._fit = () => this.fitPaper();
    window.addEventListener("resize", this._fit);
    this.$nextTick(this._fit);
    // 初始焦點不可停在日期欄:鍵盤或滾輪會直接改掉日期
    this.$nextTick(() => this.$refs.weather && this.$refs.weather.focus());
  },
  computed: {
    // 只顯示檔名與所在資料夾名稱:完整路徑太長,放在滑鼠提示
    dbName() { return this.dbPath.split(/[\\/]/).pop(); },
    dbFolder() { return this.dbPath.split(/[\\/]/).slice(-2, -1)[0] || ""; },
    month() { return this.row ? Number(this.row.date.slice(5, 7)) : ""; },
    day() { return this.row ? Number(this.row.date.slice(8, 10)) : ""; },
    dirty() { return this.row !== null && snapshot(this.form) !== this.saved; },
    sums() {
      const sum = kind => this.form[kind].reduce((total, line) => {
        const value = parseAmount(line.amount);
        return total + (Number.isNaN(value) || value === null ? 0 : value);
      }, 0);
      return { goods: sum("goods"), note: sum("note"), expense: sum("expense") };
    },
    // 以後端算好的本日數字為底,換成畫面上尚未儲存的明細合計
    view() {
      const r = this.row;
      if (!r) return {};
      const { goods, note, expense } = this.sums;
      const balance = r.revenue === null ? null : r.revenue - expense;
      return {
        expense, note, goods, balance, remain: balance,
        cum_expense: r.cum_expense - r.expense + expense,
        cum_cash: r.cum_cash + r.expense - expense,
        goods_total: r.cum_goods + goods,
      };
    },
  },
  watch: {
    dirty(value) { call("set_dirty", value).catch(() => {}); },
  },
  methods: {
    money,
    // 離開金額欄時補上千分位;不是整數就原樣留著,讓紅字提示看得到打錯的內容
    tidy(text) {
      const value = parseAmount(text);
      return value === null || Number.isNaN(value) ? String(text ?? "").trim() : money(value);
    },
    // 整張紙本盡量不捲動就看得到:放不下時等比例縮小,最小 70%,再小字就看不清楚,改用捲動
    fitPaper() {
      const paper = document.querySelector(".dr-paper");
      const box = document.querySelector(".dialog-content");
      if (!paper || !box) return;
      paper.style.zoom = 1;
      const style = getComputedStyle(box);
      box.scrollTop = 0;
      const height = box.getBoundingClientRect().bottom - parseFloat(style.paddingBottom)
        - paper.getBoundingClientRect().top;
      const zoom = Math.min(1, height / paper.getBoundingClientRect().height);
      paper.style.zoom = Math.max(0.7, zoom);
    },
    canAdd(kind) { return this.form[kind].length < LIMITS[kind]; },
    addLine(kind) {
      if (!this.canAdd(kind)) return;
      this.form[kind].push({ text: "", amount: "" });
      this.$nextTick(() => this.fitPaper());
    },
    toggleWeather(value) { this.form.weather = this.form.weather === value ? "" : value; },
    invalid(text, kind) {
      const value = parseAmount(text);
      return Number.isNaN(value) || (kind !== "expense" && value < 0);
    },
    apply(data) {
      this.row = data.row;
      this.posError = data.pos_error;
      this.dbPath = data.db_path;
      this.form = formFrom(data.row.entry);
      this.saved = snapshot(this.form);
      this.shownDate = data.row.date;
      this.$nextTick(() => this.fitPaper());
    },
    async load(date) {
      this.error = "";
      this.apply(await call("load", date));
    },
    async changeDate() {
      const target = this.date;
      if (!target || target === this.shownDate) return;
      if (this.dirty) {
        const choice = await PosConfirm.choose({
          title: "尚未儲存",
          message: `${this.shownDate} 的修改尚未儲存。`,
          items: [{ label: "儲存後切換日期", detail: "" }, { label: "放棄修改並切換日期", detail: "" }],
        });
        if (choice === 0) {
          if (!await this.save()) { this.date = this.shownDate; return; }
        } else if (choice !== 1) {
          this.date = this.shownDate;
          return;
        }
      }
      try {
        await this.load(target);
      } catch (error) {
        this.error = error.message;
        this.date = this.shownDate;
      }
    },
    payload() {
      const data = { weather: this.form.weather, payable: parseAmount(this.form.payable) };
      if (Number.isNaN(data.payable) || data.payable < 0) throw new Error("應付貨款必須是 0 以上的整數");
      for (const [kind, title] of Object.entries(TITLES)) {
        data[kind] = this.form[kind].map((line, i) => {
          const amount = parseAmount(line.amount);
          if (this.invalid(line.amount, kind))
            throw new Error(`${title}第 ${i + 1} 列的金額不正確`);
          return { text: line.text.trim(), amount };
        });
      }
      return data;
    },
    async save() {
      this.error = "";
      this.busy = true;
      try {
        this.apply(await call("save", this.shownDate, this.payload()));
        return true;
      } catch (error) {
        this.error = error.message;
        return false;
      } finally {
        this.busy = false;
      }
    },
    async chooseDb() {
      this.error = "";
      try {
        const result = await call("choose_db");
        if (!result.cancelled) {
          const keep = this.dirty ? JSON.parse(JSON.stringify(this.form)) : null;
          await this.load(this.shownDate);
          // 換資料庫只影響自動帶入的數字,未儲存的手填內容保留
          if (keep) this.form = keep;
        }
      } catch (error) {
        this.error = error.message;
      }
    },
    async exportPdf(kind) {
      this.error = "";
      if (this.dirty) {
        await PosConfirm.notify("目前的修改尚未儲存，請先儲存再匯出。");
        return;
      }
      this.busy = true;
      try {
        const result = await call({ day: "export_day", days: "export_days", month: "export_month" }[kind], this.shownDate);
        // 完整路徑沒有空格無法換行，會撐出提示框；只顯示檔名，存放位置是使用者剛選的
        if (!result.cancelled) await PosConfirm.notify(`已匯出「${result.path.split(/[\\/]/).pop()}」。`);
      } catch (error) {
        this.error = error.message;
      } finally {
        this.busy = false;
      }
    },
  },
};

Vue.createApp(window.DailyReportApp).mount("#daily-report");
