/* EU Wildlife Trade Regulations — Chatbot Widget
   Self-contained: injects its own styles and DOM.
   Usage: ChatbotWidget.init({ apiUrl, title?, placeholder? }) */
(function () {
  'use strict';

  let CFG = {
    apiUrl: '',
    title: 'Regulations Assistant',
    placeholder: 'Ask a question…',
  };
  let history = [];   // [{role, content}]
  let msgLog = [];    // [{role:'user'|'bot', text}] — for re-rendering across pages
  let busy = false;
  const SS = 'cw_session';

  function saveSession() {
    try {
      sessionStorage.setItem(SS, JSON.stringify({ history, msgLog, open: !el('cw-panel').classList.contains('cw-hide') }));
    } catch (_) {}
  }

  function loadSession() {
    try {
      const s = JSON.parse(sessionStorage.getItem(SS) || 'null');
      if (s) { history = s.history || []; msgLog = s.msgLog || []; return s; }
    } catch (_) {}
    return null;
  }

  /* ── Styles ────────────────────────────────────────────────────────────── */
  const CSS = `
    #cw-btn {
      position:fixed;bottom:24px;right:24px;z-index:9999;
      height:48px;padding:0 18px;border-radius:24px;
      background:#00703c;color:#fff;border:none;
      cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.28);
      display:flex;align-items:center;gap:8px;
      font-family:"GDS Transport",Arial,sans-serif;font-size:14px;font-weight:600;
      transition:background .2s,transform .15s;white-space:nowrap;
    }
    #cw-btn:hover{background:#004e2a;}
    #cw-btn .cw-btn-icon{font-size:18px;line-height:1;}
    #cw-btn.open .cw-btn-icon{display:none;}
    #cw-btn.open::before{content:'✕';font-size:18px;}
    #cw-panel {
      position:fixed;bottom:92px;right:24px;z-index:9998;
      width:380px;max-height:560px;
      background:#fff;border-radius:14px;
      box-shadow:0 8px 32px rgba(0,0,0,.18);
      display:flex;flex-direction:column;overflow:hidden;
      font-family:"GDS Transport",Arial,sans-serif;font-size:14px;
      transform-origin:bottom right;
      transition:transform .2s cubic-bezier(.34,1.56,.64,1),opacity .15s;
    }
    #cw-panel.cw-hide{transform:scale(.75);opacity:0;pointer-events:none;}
    #cw-head {
      background:#00703c;color:#fff;padding:13px 16px;
      font-weight:600;font-size:15px;
      display:flex;align-items:center;justify-content:space-between;
      flex-shrink:0;
    }
    #cw-x{background:none;border:none;color:#fff;font-size:22px;
          cursor:pointer;line-height:1;padding:0 2px;}
    #cw-msgs {
      flex:1;overflow-y:auto;padding:12px;
      display:flex;flex-direction:column;gap:8px;min-height:180px;
      scroll-behavior:smooth;
    }
    .cw-m {
      max-width:86%;padding:9px 13px;border-radius:12px;
      line-height:1.5;word-wrap:break-word;white-space:pre-wrap;
      font-size:13.5px;
    }
    .cw-u{align-self:flex-end;background:#00703c;color:#fff;
          border-bottom-right-radius:3px;}
    .cw-b{align-self:flex-start;background:#f2f3f4;color:#1a1a1a;
          border-bottom-left-radius:3px;}
    .cw-err{background:#fdecea!important;color:#c0392b!important;}
    .cw-typing{align-self:flex-start;background:#f2f3f4;
               padding:10px 14px;border-radius:12px;border-bottom-left-radius:3px;}
    .cw-dot{display:inline-block;width:7px;height:7px;border-radius:50%;
            background:#888;margin:0 2px;
            animation:cwb .9s ease-in-out infinite;}
    .cw-dot:nth-child(2){animation-delay:.15s;}
    .cw-dot:nth-child(3){animation-delay:.3s;}
    @keyframes cwb{0%,60%,100%{transform:translateY(0)}30%{transform:translateY(-5px)}}
    #cw-form{display:flex;padding:10px;border-top:1px solid #e5e5e5;
             gap:8px;flex-shrink:0;}
    #cw-in {
      flex:1;padding:9px 11px;border:1.5px solid #ddd;border-radius:8px;
      font-size:13.5px;outline:none;resize:none;font-family:inherit;
      min-height:44px;max-height:110px;overflow-y:auto;line-height:1.5;
    }
    #cw-in:focus{border-color:#00703c;}
    #cw-send {
      padding:9px 15px;background:#00703c;color:#fff;
      border:none;border-radius:8px;cursor:pointer;font-size:13.5px;
      transition:background .2s;align-self:flex-end;
    }
    #cw-send:hover:not(:disabled){background:#004e2a;}
    #cw-send:disabled{background:#b0b0b0;cursor:not-allowed;}
    #cw-clear{
      font-size:11px;color:#888;cursor:pointer;border:none;background:none;
      padding:0 0 6px 12px;text-decoration:underline;align-self:flex-start;
    }
    .cw-m ul{margin:4px 0;padding-left:18px;white-space:normal;}
    .cw-m ul ul{margin:2px 0;padding-left:16px;}
    .cw-m li{margin:2px 0;}
    @media(max-width:480px){
      #cw-panel{width:calc(100vw - 16px);right:8px;bottom:82px;}
      #cw-btn{font-size:13px;padding:0 14px;}
    }
  `;

  /* ── Helpers ───────────────────────────────────────────────────────────── */
  function el(id) { return document.getElementById(id); }

  function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function applyInline(line) {
    line = line.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    line = line.replace(/\*(.+?)\*/g, '<em>$1</em>');
    line = line.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return line;
  }

  function mdToHtml(text) {
    const lines = text.split('\n');
    const out = [];
    let listDepth = 0; // 0=none, 1=top-level ul, 2=nested ul

    function closeToDepth(target) {
      while (listDepth > target) {
        out.push('</ul>');
        listDepth--;
      }
    }

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const topBullet = /^[-*] (.*)/.exec(line);
      const subBullet = /^[ \t]+[-*] (.*)/.exec(line);

      if (!topBullet && !subBullet) {
        closeToDepth(0);
        if (/^#{1,3} /.test(line)) {
          out.push('<p style="margin:6px 0 2px"><strong>' + applyInline(escHtml(line.replace(/^#{1,3} /, ''))) + '</strong></p>');
        } else if (line.trim() === '') {
          out.push('<br>');
        } else {
          out.push('<p style="margin:3px 0">' + applyInline(escHtml(line)) + '</p>');
        }
        continue;
      }

      if (subBullet) {
        if (listDepth < 1) { out.push('<ul>'); listDepth = 1; }
        if (listDepth < 2) { out.push('<ul>'); listDepth = 2; }
        out.push('<li>' + applyInline(escHtml(subBullet[1])) + '</li>');
      } else {
        closeToDepth(1);
        if (listDepth < 1) { out.push('<ul>'); listDepth = 1; }
        out.push('<li>' + applyInline(escHtml(topBullet[1])) + '</li>');
      }
    }
    closeToDepth(0);
    return out.join('');
  }

  function addMsg(text, cls, isBot) {
    const msgs = el('cw-msgs');
    const d = document.createElement('div');
    d.className = 'cw-m ' + cls;
    if (isBot) {
      d.innerHTML = mdToHtml(text);
      msgs.appendChild(d);
      msgs.scrollTop = d.offsetTop - msgs.offsetTop;
    } else {
      d.textContent = text;
      msgs.appendChild(d);
      msgs.scrollTop = msgs.scrollHeight;
    }
    return d;
  }

  function showTyping() {
    const msgs = el('cw-msgs');
    const d = document.createElement('div');
    d.className = 'cw-typing'; d.id = 'cw-t';
    d.innerHTML = '<span class="cw-dot"></span><span class="cw-dot"></span><span class="cw-dot"></span>';
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
  }

  function hideTyping() { const t = el('cw-t'); if (t) t.remove(); }

  /* ── Widget build ──────────────────────────────────────────────────────── */
  function build() {
    const style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    const btn = document.createElement('button');
    btn.id = 'cw-btn'; btn.title = 'Ask our AI Assistant';
    btn.innerHTML = '<span class="cw-btn-icon">💬</span> Ask our AI Assistant';
    btn.onclick = toggle;

    const panel = document.createElement('div');
    panel.id = 'cw-panel'; panel.className = 'cw-hide';
    panel.innerHTML = `
      <div id="cw-head">
        <span>${CFG.title}</span>
        <button id="cw-x" title="Close">&#x2715;</button>
      </div>
      <div id="cw-msgs">
        <div class="cw-m cw-b">Hello! Ask me anything about EU wildlife trade regulations.</div>
      </div>
      <button id="cw-clear">Clear conversation</button>
      <form id="cw-form">
        <textarea id="cw-in" rows="2" placeholder="${CFG.placeholder}"></textarea>
        <button id="cw-send" type="submit">Send</button>
      </form>`;

    document.body.append(btn, panel);

    el('cw-x').onclick = close;
    el('cw-form').onsubmit = submit;
    el('cw-clear').onclick = clearChat;

    const inp = el('cw-in');
    inp.addEventListener('input', () => {
      inp.style.height = 'auto';
      inp.style.height = Math.min(inp.scrollHeight, 90) + 'px';
    });
    inp.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(e); }
    });

    // Restore previous session
    const session = loadSession();
    if (session && msgLog.length) {
      const msgs = el('cw-msgs');
      msgs.innerHTML = '';
      msgLog.forEach(m => {
        const d = document.createElement('div');
        d.className = 'cw-m ' + (m.role === 'user' ? 'cw-u' : 'cw-b');
        if (m.role === 'bot') { d.innerHTML = mdToHtml(m.text); } else { d.textContent = m.text; }
        msgs.appendChild(d);
      });
      msgs.scrollTop = msgs.scrollHeight;
      if (session.open) open();
    }
  }

  function toggle() { el('cw-panel').classList.contains('cw-hide') ? open() : close(); }

  function open() {
    el('cw-panel').classList.remove('cw-hide');
    el('cw-btn').classList.add('open');
    saveSession();
    setTimeout(() => el('cw-in') && el('cw-in').focus(), 250);
  }

  function close() {
    el('cw-panel').classList.add('cw-hide');
    el('cw-btn').classList.remove('open');
    saveSession();
  }

  function clearChat() {
    history = []; msgLog = [];
    sessionStorage.removeItem(SS);
    const msgs = el('cw-msgs');
    msgs.innerHTML = '<div class="cw-m cw-b">Conversation cleared. Ask me a new question!</div>';
  }

  /* ── API call ──────────────────────────────────────────────────────────── */
  async function submit(e) {
    e.preventDefault();
    if (busy) return;
    const inp = el('cw-in');
    const q = inp.value.trim();
    if (!q) return;
    inp.value = ''; inp.style.height = 'auto';
    el('cw-send').disabled = true;
    busy = true;
    msgLog.push({ role: 'user', text: q });
    addMsg(q, 'cw-u');
    showTyping();
    try {
      const res = await fetch(CFG.apiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q, history }),
      });
      hideTyping();
      const data = await res.json().catch(() => ({ error: 'Bad response' }));
      if (!res.ok || data.error) {
        addMsg(data.error || 'Something went wrong. Please try again.', 'cw-m cw-b cw-err', false);
      } else {
        addMsg(data.answer, 'cw-b', true);
        msgLog.push({ role: 'bot', text: data.answer });
        history.push({ role: 'user', content: q });
        history.push({ role: 'assistant', content: data.answer });
        if (history.length > 20) history = history.slice(-20);
        // Nudge after 6 exchanges (12 history entries)
        if (history.length === 12) {
          const nudge = document.createElement('div');
          nudge.className = 'cw-m cw-b';
          nudge.style.cssText = 'background:#fef9e7;color:#7d6608;font-size:12px;font-style:italic;';
          nudge.textContent = 'Tip: starting a new topic? Use "Clear conversation" above to keep responses focused.';
          el('cw-msgs').appendChild(nudge);
        }
      }
    } catch {
      hideTyping();
      addMsg('Could not reach the server. Please check your connection.', 'cw-m cw-b cw-err');
    }
    saveSession();
    busy = false;
    el('cw-send').disabled = false;
    el('cw-in').focus();
  }

  /* ── Public API ────────────────────────────────────────────────────────── */
  window.ChatbotWidget = {
    init(config) {
      Object.assign(CFG, config);
      if (!CFG.apiUrl) { console.error('ChatbotWidget: apiUrl is required'); return; }
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', build);
      } else {
        build();
      }
    },
  };
}());
