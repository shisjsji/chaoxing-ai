/**
 * 把 morphicons 接到页面上：定义 <morph-icon>，并把图标数据挂到 window。
 *
 * 用 ESM 加载，所以必须走 http（程序本身就是 Flask 起的服务）。
 * 这个模块失败也不会影响主界面 —— 主脚本拿不到 MORPH_ICONS 就会跳过图标。
 */
import { defineMorphIcon } from "./vendor/morphicons/element.js";
import * as icons from "./vendor/icons.js";

defineMorphIcon();

window.MORPH_ICONS = icons;

window.morphIcon = function (icon, opts) {
  const o = opts || {};
  const el = document.createElement("morph-icon");
  el.setAttribute("size", String(o.size || 18));
  el.setAttribute("stroke-width", String(o.strokeWidth || 1.75));
  el.setAttribute("shape-rendering", "geometricPrecision");
  el.setAttribute("spring", "snappy");
  el.setAttribute("reduced-motion", "user");
  if (o.color) el.setAttribute("color", o.color);
  el.icon = icon;
  return el;
};

if (typeof window.initIcons === "function") {
  window.initIcons();
}