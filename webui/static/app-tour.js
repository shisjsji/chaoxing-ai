/* 新手教程 */

/* ---------- 新手教程 ---------- */
const ILLU = {
  welcome: `<svg viewBox="0 0 320 150" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <rect x="58" y="30" width="204" height="94" rx="8" opacity=".5"/>
    <rect x="70" y="42" width="180" height="70" rx="6"/>
    <path d="M70 58h180" opacity=".5"/>
    <circle cx="80" cy="50" r="2.4" fill="currentColor" stroke="none"/>
    <circle cx="89" cy="50" r="2.4" fill="currentColor" stroke="none" opacity=".5"/>
    <path d="M86 80h56M86 92h88" opacity=".7"/>
    <path d="M232 80l14 8-14 8z" fill="currentColor" stroke="none" opacity=".85"/>
  </svg>`,
  account: `<svg viewBox="0 0 320 150" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <rect x="64" y="34" width="192" height="82" rx="9"/>
    <path d="M64 52h192" opacity=".5"/>
    <rect x="78" y="64" width="86" height="14" rx="4" opacity=".65"/>
    <rect x="78" y="88" width="120" height="14" rx="4" opacity=".65"/>
    <path d="M196 64h44" opacity=".35"/>
    <circle cx="248" cy="52" r="5" opacity=".8"/>
    <path d="M36 76a12 12 0 0 1 12-12h6" opacity=".0"/>
    <rect x="246" y="88" width="24" height="14" rx="4" fill="currentColor" stroke="none" opacity=".85"/>
  </svg>`,
  key: `<svg viewBox="0 0 320 150" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="104" cy="75" r="22"/>
    <circle cx="104" cy="75" r="8" opacity=".5"/>
    <path d="M126 75h94"/>
    <path d="M196 75v16M212 75v11"/>
    <path d="M150 40h96" opacity=".3"/>
    <path d="M150 110h64" opacity=".3"/>
    <path d="M60 48l6 6M60 102l6-6" opacity=".5"/>
  </svg>`,
  run: `<svg viewBox="0 0 320 150" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <rect x="58" y="36" width="204" height="78" rx="9" opacity=".55"/>
    <circle cx="86" cy="56" r="4" fill="currentColor" stroke="none"/>
    <path d="M100 56h74" opacity=".5"/>
    <rect x="74" y="74" width="120" height="10" rx="5" opacity=".45"/>
    <rect x="74" y="74" width="76" height="10" rx="5" fill="currentColor" stroke="none" opacity=".8"/>
    <rect x="74" y="92" width="140" height="10" rx="5" opacity=".45"/>
    <rect x="74" y="92" width="40" height="10" rx="5" fill="currentColor" stroke="none" opacity=".55"/>
    <path d="M216 66l22 11-22 11z" fill="currentColor" stroke="none" opacity=".9"/>
  </svg>`,
  library: `<svg viewBox="0 0 320 150" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <ellipse cx="160" cy="52" rx="46" ry="14"/>
    <path d="M114 52v42c0 8 21 14 46 14s46-6 46-14V52"/>
    <path d="M114 73c0 8 21 14 46 14s46-6 46-14" opacity=".5"/>
    <path d="M92 118h136" opacity=".35"/>
    <path d="M124 108l10 8 20-18" opacity=".85"/>
  </svg>`,
};

const TOUR = [
  {
    title: '把重复的刷课交给程序',
    illu: 'welcome',
    text: `<p>这个程序帮你把学习通的视频任务点、章节测验和考试自动跑完，进度和日志都看得见。</p>
      <ul><li>视频自动播放、播完自动进下一节</li>
      <li>章节测验先查题库，没命中再问大模型</li>
      <li>题库只复用验证过的答案，猜的不会拿出来用</li></ul>`,
  },
  {
    title: '第一步：填学习通账号',
    illu: 'account',
    text: `<p>到「设置 → 学习通账号」填手机号和密码，点「拉取课程列表」，勾选要跑的课。</p>
      <ul><li>密码和 API Key 用 Windows 加密存在本机，不上传服务器</li>
      <li>只刷勾选的课程，不勾会问你要不要刷全部</li></ul>`,
  },
  {
    title: '第二步：配一个大模型',
    illu: 'key',
    text: `<p>题库没命中时需要模型兜底。到 <b>设置 → 大模型</b> 填写接口地址和 API Key：</p>
      <ul>
        <li>推荐 DeepSeek：到 <b>platform.deepseek.com</b> 注册，在「API keys」里新建一个，<b>sk-</b> 开头</li>
        <li>接口地址保持 <b>https://api.deepseek.com</b>，模型选 <b>deepseek-chat</b></li>
        <li>填完点「获取模型」再点「测试连接」，两个都通过就能用</li>
      </ul>`,
  },
  {
    title: '第三步：点开始，然后去忙别的',
    illu: 'run',
    text: `<p>回到「概览」点右上角「开始运行」。程序按课程并发处理，进度逐章显示，随时可以停。</p>
      <ul><li>关掉窗口不会退出，会收进系统托盘继续跑</li>
      <li>速度、并发、考试节奏都在「高级设置」抽屉里</li></ul>`,
  },
  {
    title: '题库与考试怎么用',
    illu: 'library',
    text: `<p>答过的题会进本地题库。只有<b>平台判分满分</b>、<b>双答一致</b>或<b>共享库多人一致</b>的答案才会被复用。</p>
      <ul>
        <li>考试同样先查题库；图形验证码和人脸验证需要你自己完成</li>
        <li>题库页能看到每道题的「依据」，也能手工确认或标错</li>
      </ul>`,
  },
];

const TOUR_KEY = 'chaoxing.tour.v1';
let tourStep = 0;

function renderTour() {
  const item = TOUR[tourStep];
  $('wl-title').textContent = item.title;
  $('wl-illu').innerHTML = ILLU[item.illu] || '';
  $('wl-text').innerHTML = item.text;
  $('wl-dots').innerHTML = TOUR.map((_, i) => `<i class="${i === tourStep ? 'on' : ''}"></i>`).join('');
  $('wl-prev').style.display = tourStep ? '' : 'none';
  $('wl-next').textContent = tourStep === TOUR.length - 1 ? '开始使用' : '下一步';
}

function openTour() {
  tourStep = 0;
  renderTour();
  $('welcome').hidden = false;
}

function closeTour() {
  const box = $('welcome');
  box.classList.add('closing');
  try { localStorage.setItem(TOUR_KEY, 'seen'); } catch (e) {}
  setTimeout(() => { box.hidden = true; box.classList.remove('closing'); }, 200);
}

$('wl-next').onclick = () => {
  if (tourStep < TOUR.length - 1) { tourStep++; renderTour(); return; }
  closeTour();
};
$('wl-prev').onclick = () => { if (tourStep > 0) { tourStep--; renderTour(); } };
$('wl-skip').onclick = closeTour;
$('btnTour').onclick = openTour;

try {
  if (!localStorage.getItem(TOUR_KEY)) openTour();
} catch (e) { openTour(); }
