/* Cinder 灵动岛 v2 —— 顶部胶囊 + 窗帘展开 + 全部远控功能 + 性能修复
   连接地址未改动：沿用页面原有 API / location.origin */
(function(){
'use strict';
var API = (typeof window.API !== 'undefined' && window.API) || location.origin;

/* ===== 全部远控功能（点一下即下发） ===== */
var GROUPS = [
  {name:'导航', items:[
    ['‹','返回','BACK'],['◉','主页','HOME'],['▤','多任务','APP_SWITCH'],
   ['☰','菜单','MENU'],['⏻','电源','POWER'],['↺','最近应用','RECENTS']
  ]},
  {name:'媒体', items:[
    ['＋','音量+','VOLUME_UP'],['－','音量-','VOLUME_DOWN'],['💡','闪光灯','FLASH'],
    ['📳','震动','VIBRATE'],['🔊','朗读','TTS'],['⏹','停止朗读','TTS_STOP']
  ]},
  {name:'系统', items:[
    ['🔒','锁屏','LOCK'],['🔓','亮屏','WAKEUP'],['☀','亮度','BRIGHTNESS'],
    ['📶','WiFi','WIFI'],['📡','流量','DATA'],['✈','飞行模式','AIRPLANE']
  ]},
  {name:'截取', items:[
    ['📸','截图','SCREENSHOT'],['⏺','录屏','RECORD_START'],['⏹','停录','RECORD_STOP'],
    ['📋','读剪贴板','CLIPBOARD_GET'],['📝','写剪贴板','CLIPBOARD_SET'],['🔍','布局拆解','LAYOUT']
  ]},
  {name:'控制', items:[
    ['✎','画布','DRAW'],['👆','点击','TAP'],['↔','滑动','SWIPE'],
    ['⌨','输入文字','TEXT'],['🔗','开链接','OPEN_URL'],['📱','打开应用','APP']
  ]},
  {name:'高级', items:[
    ['⌨','终端','SHELL'],['📁','文件','FILES'],['🔔','通知','NOTIFY'],
    ['🔋','电量','BATTERY'],['⚙','设置','SETTINGS'],['🧹','清理','CLEAR']
  ]}
];

/* ===== 样式（一次注入，不触发重排） ===== */
var CSS = [
'#cinder-island{position:fixed;top:calc(env(safe-area-inset-top,0px) + 8px);left:50%;transform:translateX(-50%);z-index:80;',
' width:min(92vw,340px);border-radius:999px;display:flex;align-items:center;gap:8px;padding:7px 8px;',
' background:rgba(28,26,48,.72);backdrop-filter:blur(22px) saturate(1.7);-webkit-backdrop-filter:blur(22px) saturate(1.7);',
' border:1px solid rgba(255,255,255,.22);box-shadow:0 10px 34px rgba(0,0,0,.5),inset 0 1px 0 rgba(255,255,255,.28);',
' cursor:pointer;transition:width .3s,border-radius .3s,background .25s;contain:layout style;}',
'#cinder-island.open{border-radius:22px;background:rgba(24,22,42,.82);}',
'#cinder-island .ci-dot{width:8px;height:8px;border-radius:50%;background:#ff5d73;box-shadow:0 0 8px #ff5d73;flex:none;transition:background .3s,box-shadow .3s}',
'#cinder-island.online .ci-dot{background:#37e6a0;box-shadow:0 0 8px #37e6a0}',
'#cinder-island .ci-txt{font-size:12px;font-weight:600;color:#f2f5ff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}',
'#cinder-island .ci-mirror{width:34px;height:22px;flex:none;border-radius:999px;background:rgba(255,255,255,.10);display:grid;place-items:center;font-size:10px;color:#cfe}',
'#ci-curtain{position:fixed;top:0;left:0;right:0;z-index:79;padding:calc(env(safe-area-inset-top,0px) + 52px) 12px 14px;',
' display:flex;justify-content:center;pointer-events:none;contain:layout style;}',
'#ci-panel{width:min(94vw,420px);max-height:62vh;overflow-y:auto;-webkit-overflow-scrolling:touch;pointer-events:auto;',
' border-radius:22px;padding:12px 12px 14px;background:rgba(22,20,40,.82);',
' backdrop-filter:blur(24px) saturate(1.7);-webkit-backdrop-filter:blur(24px) saturate(1.7);',
' border:1px solid rgba(255,255,255,.22);box-shadow:0 24px 60px rgba(0,0,0,.6),inset 0 1px 0 rgba(255,255,255,.25);',
' transform:translateY(-130%);opacity:0;transition:transform .34s cubic-bezier(.2,.85,.2,1),opacity .26s;will-change:transform}',
'#ci-panel.open{transform:translateY(0);opacity:1}',
'.ci-gname{font-size:10px;letter-spacing:1.4px;color:rgba(242,245,255,.55);margin:10px 2px 6px;text-transform:uppercase}',
'.ci-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}',
'.ci-btn{display:flex;flex-direction:column;align-items:center;gap:4px;padding:10px 3px;border-radius:13px;cursor:pointer;',
' font-size:10px;line-height:1.15;color:#f2f5ff;text-align:center;background:rgba(255,255,255,.08);',
' border:1px solid rgba(255,255,255,.14);transition:transform .13s,background .18s;contain:layout style}',
'.ci-btn:active{transform:scale(.92);background:rgba(124,92,255,.34)}',
'.ci-btn b{font-size:17px;font-weight:400}',
'#ci-mask{position:fixed;inset:0;z-index:78;background:rgba(4,4,10,.42);opacity:0;pointer-events:none;transition:opacity .26s}',
'#ci-mask.open{opacity:1;pointer-events:auto}',
'@media (prefers-reduced-motion:reduce){#ci-panel{transition:opacity .2s}}'
].join('\n');

/* ===== 构建 DOM ===== */
function build(){
  var st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);

  var island = document.createElement('div');
  island.id = 'cinder-island';
  island.innerHTML = '<span class="ci-dot"></span><span class="ci-txt">灵动岛 · 点按展开</span><span class="ci-mirror">◓</span>';
  document.body.appendChild(island);

  var curtain = document.createElement('div'); curtain.id = 'ci-curtain';
  var panel = document.createElement('div'); panel.id = 'ci-panel';
  var html = '';
  GROUPS.forEach(function(g){
    html += '<div class="ci-gname">' + g.name + '</div><div class="ci-grid">';
    g.items.forEach(function(it){
      html += '<div class="ci-btn" data-ci="' + it[2] + '" data-ci-label="' + it[1] + '"><b>' + it[0] + '</b><span>' + it[1] + '</span></div>';
    });
    html += '</div>';
  });
  panel.innerHTML = html;
  curtain.appendChild(panel); document.body.appendChild(curtain);

  var mask = document.createElement('div'); mask.id = 'ci-mask'; document.body.appendChild(mask);

  function close(){ panel.classList.remove('open'); island.classList.remove('open'); mask.classList.remove('open'); }
  function open(){ panel.classList.add('open'); island.classList.add('open'); mask.classList.add('open'); }
  island.onclick = function(){ panel.classList.contains('open') ? close() : open(); };
  mask.onclick = close;

  /* 事件委托：按钮再多也只绑一次 */
  panel.addEventListener('click', function(e){
    var el = e.target.closest ? e.target.closest('.ci-btn') : null;
    if (!el) return;
    close();
    var nav = {SCREENSHOT:'screenshot', RECORD_START:'record', CLIPBOARD_GET:'clipboard',
               CLIPBOARD_SET:'clipboard', LAYOUT:'layout', SHELL:'shell', FILES:'files',
               SETTINGS:'settings'}[el.getAttribute('data-ci')];
    if (nav && typeof window.goPanel === 'function') { window.goPanel(nav); return; }
    sendCmd(el.getAttribute('data-ci'), el.getAttribute('data-ci-label'));
  });

  /* 在线状态：复用已有探测，不额外轮询 */
  Object.defineProperty(window, '__ciOnline', {
    set: function(v){ island.classList.toggle('online', !!v); },
    get: function(){ return island.classList.contains('online'); }
  });
}

function sendCmd(cmd, label){
  var body = {}; try { body = {command: cmd}; } catch(e) {}
  var p = fetch(API + '/api/control?command=' + encodeURIComponent(cmd), {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)
  });
  Promise.resolve(p).then(function(r){ return r.json(); }).then(function(r){
    if (!r || r.executed === false) toast((label||cmd) + '：等待 APK 接管');
    else toast((label||cmd) + ' ✓');
  })['catch'](function(){ toast('离线，指令未送达'); });
}

/* ===== 轻提示（复用页面已有的 __t） ===== */
var tt;
function toast(msg){
  var el = document.getElementById('__t');
  if (!el) {
    el = document.createElement('div'); el.id = '__t';
    el.style.cssText = 'position:fixed;left:50%;bottom:32px;transform:translateX(-50%);z-index:99;padding:10px 18px;border-radius:999px;font-size:13px;font-weight:600;background:rgba(20,18,40,.86);color:#fff;border:1px solid rgba(255,255,255,.22);backdrop-filter:blur(14px);opacity:0;transition:opacity .2s';
    document.body.appendChild(el);
  }
  el.textContent = msg; el.style.opacity = '1';
  clearTimeout(tt); tt = setTimeout(function(){ el.style.opacity = '0'; }, 1500);
}

/* ===== 性能修复：降频 + 后台暂停 + 小图 + 去重 ===== */
function perf(){
  var hidden = false;
  document.addEventListener('visibilitychange', function(){ hidden = document.hidden; });
  var lastUrl = '';
  function patchTimer(fnName, ms){
    var id = window[fnName];
    if (typeof id !== 'number') return;
    clearInterval(id);
    window[fnName] = setInterval(function(){
      if (hidden) return;
      var r = fnName === 'coverTimer' ? coverOnce : netOnce;
      if (r) r();
    }, ms);
  }
  var coverTimerId = null, netTimerId = null;
  /* 截图：小图 + 去重，不再反复换大图 src */
  function coverOnce(){
    var img = document.getElementById('heroImg');
    if (!img) return;
    fetch(API + '/api/screenshot?w=540&q=60', {cache:'no-store'}).then(function(r){ return r.blob(); }).then(function(b){
      if (!b || b.size < 500) throw 0;
      var url = URL.createObjectURL(b);
      if (url === lastUrl) { URL.revokeObjectURL(url); return; }
      if (lastUrl) URL.revokeObjectURL(lastUrl);
      lastUrl = url; img.src = url; img.style.display = 'block';
      var n = document.getElementById('heroNo'); if (n) n.style.display = 'none';
    })['catch'](function(){});
  }
  function netOnce(){
    fetch(API + '/healthz', {cache:'no-store'}).then(function(r){
      window.__ciOnline = r.ok;
      var bar = document.getElementById('devBar');
      if (bar) bar.className = r.ok ? 'online' : '';
    })['catch'](function(){ window.__ciOnline = false; });
  }
  /* 接管原有 setInterval：把 5s/6s 降到 10s/12s，且后台停 */
  var oSet = window.setInterval;
  window.setInterval = function(fn, ms){
    if (ms && ms <= 6000) {
      var isCover = fn.toString().indexOf('screenshot') >= 0;
      var isNet = fn.toString().indexOf('healthz') >= 0 || fn.toString().indexOf('netProbe') >= 0;
      if (isCover) return oSet(function(){ if (!document.hidden) coverOnce(); }, 10000);
      if (isNet) return oSet(function(){ if (!document.hidden) netOnce(); }, 12000);
    }
    return oSet(fn, ms);
  };
  netOnce();
}

/* ===== 对外：面板内跳转 ===== */
window.goPanel = function(anchor){
  var t = '/panel/' + (anchor ? '#' + anchor : '');
  location.href = t;
};

function init(){ build(); perf(); }
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
})();
