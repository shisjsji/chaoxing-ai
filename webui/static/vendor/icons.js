/**
 * 界面图标数据（24×24 线性风格，全部用 <path> 表达，方便做形变动画）。
 *
 * 形状尽量保持"可形变"：两端路径数差太多时 morphicons 会自己拆分补齐，
 * 但同数量、同方向的形状过渡最自然。
 */

export const Grid = [
  ["path", { d: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z" }],
];

export const Exam = [
  ["path", { d: "M4 5h16v14H4z" }],
  ["path", { d: "M8 12l3 3 5-6" }],
];

export const Library = [
  ["path", { d: "M5 5h5v14H5z" }],
  ["path", { d: "M13 5h6v14h-6z" }],
];

export const Sliders = [
  ["path", { d: "M4 7h3M4 17h7" }],
  ["path", { d: "M11 7h9M15 17h5" }],
  ["path", { d: "M9 5v4M13 15v4" }],
];

export const Play = [
  ["path", { d: "M8 5v14l11-7z" }],
];

export const Pause = [
  ["path", { d: "M9 5v14" }],
  ["path", { d: "M15 5v14" }],
];

export const Stop = [
  ["path", { d: "M6 6h12v12H6z" }],
];

/** 日志：面板 + 指向面板内的箭头；展开后箭头翻到另一边 */
export const PanelLeft = [
  ["path", { d: "M4 5h16v14H4z" }],
  ["path", { d: "M14 5v14" }],
  ["path", { d: "M11 9l-3 3 3 3" }],
];

export const PanelRight = [
  ["path", { d: "M4 5h16v14H4z" }],
  ["path", { d: "M14 5v14" }],
  ["path", { d: "M7 9l3 3-3 3" }],
];

export const Message = [
  ["path", { d: "M4 5h16v11H9l-5 4z" }],
];

export const Check = [
  ["path", { d: "M5 13l4 4L19 7" }],
];

export const Download = [
  ["path", { d: "M12 4v10" }],
  ["path", { d: "M8 11l4 4 4-4" }],
  ["path", { d: "M5 19h14" }],
];

export const Refresh = [
  ["path", { d: "M20 12a8 8 0 1 1-2.4-5.7" }],
  ["path", { d: "M20 4v5h-5" }],
];

export const ArrowRight = [
  ["path", { d: "M5 12h13" }],
  ["path", { d: "M13 6l6 6-6 6" }],
];

export const ArrowLeft = [
  ["path", { d: "M19 12H6" }],
  ["path", { d: "M11 6l-6 6 6 6" }],
];

export const Close = [
  ["path", { d: "M6 6l12 12" }],
  ["path", { d: "M18 6L6 18" }],
];

export const Sparkle = [
  ["path", { d: "M12 4l1.7 4.3L18 10l-4.3 1.7L12 16l-1.7-4.3L6 10l4.3-1.7z" }],
];

export const Key = [
  ["path", { d: "M9 8a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" }],
  ["path", { d: "M12 11h8" }],
  ["path", { d: "M17 11v3M20 11v2" }],
];

export const Monitor = [
  ["path", { d: "M4 5h16v11H4z" }],
  ["path", { d: "M9 20h6M12 16v4" }],
];

export const Database = [
  ["path", { d: "M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z" }],
  ["path", { d: "M4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7" }],
];

export const Shield = [
  ["path", { d: "M12 3l7 3v6c0 4-3 7.5-7 9-4-1.5-7-5-7-9V6z" }],
];