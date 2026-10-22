// 使用專案正式 Vue prod 編譯器與 renderer；只替換 Node 不提供的 DOM host。
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
const path = require("path"), root = process.argv[1];
const errors = [], calls = [], events = {};
const context = {console, setTimeout, clearTimeout, setInterval, clearInterval};
context.document = {createElement: tag=>node(tag), activeElement:null,
  addEventListener(name, fn){events[name]=fn;}, removeEventListener(){}};
context.window = {PosPages: {}, PosEmployee: {id: 1}, addEventListener(){}, removeEventListener(){},
  PosDesktopLock: {lock(){}, unlock(){}}, fmtAttr: row => row.attr_display || ""};
context.API = {};
context.PosConfirm = {ask: async () => true, notify: async () => {}};
vm.createContext(context);
const load = file => vm.runInContext(fs.readFileSync(path.join(root, file), "utf8"), context, {filename: file});
load("static/js/vue.global.prod.js");
const Vue = context.Vue;
context.window.PosAuth = Vue.reactive({isAdmin: false, pendingStocktakes: 0});
context.window.PosAuthOps = {refresh: async () => {calls.push(["refresh"]);}};
load("static/js/pos_shared.js");
const html = fs.readFileSync(path.join(root, "static/index.html"), "utf8");
const template = id => html.split(`<template id="tpl-${id}">`)[1].split(/<template id=/)[0]
  .replace(/<\/template>\s*$/, "");
function node(tag, text = "") {
  return {tag, tagName: tag.toUpperCase(), nodeType: tag === "#text" ? 3 : tag === "#comment" ? 8 : 1,
    text, children: [], props: {}, listeners: {}, style: {}, value: "", _value: "",
    get options() {return this.children.filter(n => n.tag === "option");},
    addEventListener(name, fn) {(this.listeners[name] ||= []).push(fn);},
    removeEventListener(){}, setAttribute(k,v){this.props[k]=v;}, removeAttribute(k){delete this.props[k];},
    focus(){}, select(){}};
}
const renderer = Vue.createRenderer({
  createElement: tag => node(tag), createText: text => node("#text", text), createComment: text => node("#comment", text),
  setText: (n,text) => {n.text=text;}, setElementText: (n,text) => {n.text=text;n.children=[];},
  parentNode: n => n.parent, nextSibling: n => n.parent?.children[n.parent.children.indexOf(n)+1] || null,
  insert(n,parent,anchor=null) {
    if(n.parent) n.parent.children.splice(n.parent.children.indexOf(n),1);
    n.parent=parent;
    const i=anchor ? parent.children.indexOf(anchor) : -1;
    if(i<0) parent.children.push(n); else parent.children.splice(i,0,n);
  },
  remove(n){if(n.parent)n.parent.children.splice(n.parent.children.indexOf(n),1);n.parent=null;},
  patchProp(n,k,old,value){n.props[k]=value;if(k==="value"){n.value=value;n._value=value;}if(k==="multiple")n.multiple=value;},
});
function compile(source) {
  return Vue.compile(source, {hoistStatic:false, decodeEntities:s=>s.replace(/&gt;/g,">").replace(/&lt;/g,"<").replace(/&amp;/g,"&")});
}
function mount(definition, source, extra={}) {
  const baseData=definition.data;
  const component={...definition, template:undefined, mounted:undefined, unmounted:undefined,
    data(){return {...(baseData ? baseData.call(this) : {}),...extra};}, render:compile(source)};
  const app=renderer.createApp(component);
  app.mixin(context.window.PosMixin);
  app.provide("showError",m=>errors.push(m));app.provide("goPage",(...args)=>calls.push(["goPage",...args]));
  const dom=node("root"), state=app.mount(dom);
  return {state,dom,app};
}
const all = (n,p) => [...(p(n)?[n]:[]), ...n.children.flatMap(child=>all(child,p))];
const text = n => n.tag === "#comment" ? "" : String(n.text) + n.children.map(text).join("");
const buttons = dom => all(dom,n=>n.tag==="button");
const button = (dom,label) => buttons(dom).find(n=>text(n)===label);
const click = n => {assert.ok(n,"找不到按鈕");return n.props.onClick({target:n,preventDefault(){}});};
const tick = async () => {await Vue.nextTick();await Promise.resolve();await Vue.nextTick();};
const API=context.API, auth=context.window.PosAuth;
