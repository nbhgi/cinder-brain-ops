/* 面板侧 WebSocket 桥接：把 /ws 的消息接到现有 UI 上
   在 static/index.html 与 static/home.html 的 <script> 内、原有轮询逻辑之后粘贴 */
(function () {
  var WS = (location.protocol === 'https:' ? 'wss://' : 'ws://') +
           location.host + '/ws?role=client';
  var LAST = 0, IMG = null;

  function connect() {
    var s = new WebSocket(WS);
    s.onopen = function () {
      setAgentDot(true);               // 复用现有顶部绿点逻辑
      s.send(JSON.stringify({ t: 'ping' }));
      setInterval(function () {
        if (s.readyState === 1) s.send(JSON.stringify({ t: 'ping' }));
      }, 20000);
    };
    s.onmessage = function (ev) {
      var m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.t === 'agent' && m.d) {
        if (m.d.online !== undefined) setAgentDot(!!m.d.online);
        if (m.d.frame && m.d.frame.b64) {
          var now = Date.now();
          if (now - LAST < 120) return;                 // 节流，防卡顿
          LAST = now;
          var url = 'data:image/jpeg;base64,' + m.d.frame.b64;
          var card = document.querySelector('#video img') ||
                     document.querySelector('[data-liveimg]');
          if (card) { card.src = url; }
          else if (IMG) { IMG.src = url; }
          var fpsEl = document.querySelector('[data-fps]');
          if (fpsEl) fpsEl.textContent = Math.round(1000 / Math.max(1, now - (LAST - 120))) + ' fps';
        }
      }
    };
    s.onclose = function () { setAgentDot(false); setTimeout(connect, 3000); };
    s.onerror = function () { try { s.close(); } catch (e) {} };
  }
  function setAgentDot(on) {
    var dot = document.querySelector('#island .dot, .cap .dot, [data-agent-dot]');
    if (dot) dot.style.background = on ? '#34c759' : '#ff3b30';
    var t = document.querySelector('[data-agent-text]');
    if (t) t.textContent = on ? '设备已连接' : '未连接设备';
  }
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', connect);
  else connect();
})();
