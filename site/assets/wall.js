/* WALL — shared page logic. Reads the agent's files, renders the wall. No framework, no build. */
window.WALL = (function () {
  // production hosts read the agent; anywhere else (localhost, previews, file://) renders the bundled sample data
  const SAMPLE = !/(^|\.)wallpad\.run$|\.netlify\.app$/.test(location.hostname);
  // where the agent lives: the domain once DNS exists, the box's sslip.io name until then
  const HOSTS = window.WALL_API ? [window.WALL_API] : ['https://api.wallpad.run', 'https://162-35-122-197.sslip.io'];
  let API = SAMPLE ? 'sample' : HOSTS[0].replace(/\/$/, '');
  let DEMO = SAMPLE;
  function demoBanner(text) {
    if (document.getElementById('demo-banner')) return;
    const d = document.createElement('div'); d.id = 'demo-banner';
    d.style.cssText = "background:#53fc18;color:#070907;font:600 12px/1.4 'Manrope',system-ui,sans-serif;letter-spacing:.02em;padding:8px 16px;text-align:center";
    d.textContent = text; document.body.prepend(d);
  }
  // production tries the agent; if it is not reachable yet, the bundled sample data renders instead and says so
  async function useSampleIfAgentDown() {
    if (API === 'sample') return;
    for (const h of HOSTS) {
      try {
        const c = new AbortController(); const t = setTimeout(() => c.abort(), 4000);
        const r = await fetch(`${h.replace(/\/$/, '')}/state.json`, {cache: 'no-store', signal: c.signal}); clearTimeout(t);
        if (r.ok) { API = h.replace(/\/$/, ''); return; }
      } catch (e) { /* try the next host */ }
    }
    API = 'sample'; DEMO = true;
    demoBanner('Demo. The agent is not online yet, so channels and frames are real but viewer counts, chat numbers and coins are sample data.');
  }
  const X_URL = window.WALL_X || 'https://x.com/wallpad';
  const $ = (s, el) => (el || document).querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = n => { n = +n || 0; return n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e3 ? (n/1e3).toFixed(n >= 1e4 ? 0 : 1)+'K' : String(Math.round(n)); };
  const usd = n => n == null ? '–' : '$' + fmt(n);
  const ago = ts => { const d = Math.max(0, Math.floor(Date.now()/1000 - ts)); return d < 60 ? d+'s' : d < 3600 ? Math.floor(d/60)+'m' : d < 86400 ? Math.floor(d/3600)+'h' : Math.floor(d/86400)+'d'; };
  const sol = l => { const v = (l || 0) / 1e9; return v >= 100 ? v.toFixed(1) : v >= 1 ? v.toFixed(3) : v.toFixed(4); };
  const tok = raw => fmt((raw || 0) / 1e6);
  const tx = (sig, label) => sig ? `<a href="https://solscan.io/tx/${esc(sig)}" target="_blank" rel="noopener">${esc(label || 'tx')} ↗</a>` : '<span class="d">–</span>';
  const mcaps = {};

  let probed = null;
  async function get(name) {
    if (!probed) probed = useSampleIfAgentDown();
    await probed;
    const r = await fetch(`${API}/${name}`, {cache: 'no-store'});
    if (!r.ok) throw new Error(`${name}: http ${r.status}`);
    return r.json();
  }
  const clipUrl = p => !p ? '' : p.startsWith('http') ? p : `${API}/${p}`;
  // a frame that never fails: tries each source in turn, then a tile with the streamer's initial
  const bust = (u, plat) => (plat === 'twitch' && /previews-ttv/.test(u)) ? u + '?t=' + Math.floor(Date.now() / 300000) : u;
  function pic(srcs, plat, name, cls, lazy = true) {
    const list = (srcs || []).filter(Boolean).map(u => bust(clipUrl(u), plat));
    const first = list.shift();
    const alts = esc(list.join('|'));
    const chain = esc([first].concat(list).filter(Boolean).join('|'));   // what this picture was asked to show; see morphCard
    if (!first) return `<span class="noart ${cls || ''}" data-chain="" data-i="${esc((name || '?').slice(0, 1).toUpperCase())}"></span>`;
    return `<img class="${cls || ''}" src="${esc(first)}" alt="" loading="${lazy ? 'lazy' : 'eager'}" decoding="async" data-alt="${alts}" data-chain="${chain}" onerror="WALL.imgFail(this)">`;
  }
  function imgFail(img) {
    const alts = (img.dataset.alt || '').split('|').filter(Boolean);
    if (alts.length) { img.dataset.alt = alts.slice(1).join('|'); img.src = alts[0]; return; }
    const tile = document.createElement('span'); tile.className = 'noart ' + img.className;
    const name = (img.closest('[data-name]') || {}).dataset ? (img.closest('[data-name]').dataset.name || '?') : '?';
    tile.dataset.i = name.slice(0, 1).toUpperCase();
    tile.dataset.chain = img.dataset.chain || '';
    img.replaceWith(tile);
  }
  // a card's numbers change every tick; its picture does not. a re-render patches the card around the picture,
  // so the <img> (or the tile it fell back to) never leaves the document and never starts loading over.
  function morphCard(art, fresh, flash) {
    art.classList.remove('upd'); if (flash) void art.offsetWidth;
    art.className = fresh.className + (flash ? ' upd' : '');
    art.dataset.name = fresh.dataset.name || '';
    const oa = art.querySelector(':scope > .thumb'), na = fresh.querySelector(':scope > .thumb');
    if (oa && na) {
      oa.href = na.href;
      const op = oa.querySelector(':scope > img, :scope > .noart'), np = na.querySelector(':scope > img, :scope > .noart');
      if (op && np && op.dataset.chain === np.dataset.chain) {
        // same picture: it stays where it is, never leaves the document; only the labels around it are swapped
        [...oa.children].forEach(c => { if (c !== op) c.remove(); });
        [...na.children].forEach(c => { if (c !== np) oa.append(c); });
      } else oa.replaceChildren(...na.children);
    }
    const ob = art.querySelector(':scope > .body'), nb = fresh.querySelector(':scope > .body');
    if (ob && nb) ob.replaceWith(nb);
  }

  /* the logo: a pixel clipper in a purple hoodie, headset on, phone up, walking. animated in css */
  const LOGO = `<svg viewBox="0 -7 26 41" aria-hidden="true" shape-rendering="crispEdges">
<ellipse class="sh" cx="12" cy="32.2" rx="8" ry="1.2" fill="rgba(0,0,0,.6)"/>
<g class="body">
 <rect x="7" y="0" width="10" height="2" fill="#7c3aed"/><rect x="6" y="2" width="12" height="2" fill="#7c3aed"/><rect x="6" y="4" width="2" height="7" fill="#7c3aed"/><rect x="16" y="4" width="2" height="7" fill="#7c3aed"/>
 <rect x="8" y="4" width="8" height="7" fill="#f4c9a4"/><rect x="9" y="6" width="2" height="2" fill="#14161a"/><rect x="13" y="6" width="2" height="2" fill="#14161a"/><rect x="10" y="9" width="4" height="1" fill="#c97d6a"/>
 <rect x="7" y="1" width="10" height="1" fill="#53fc18"/><rect x="4" y="5" width="3" height="4" fill="#53fc18"/><rect x="17" y="5" width="3" height="4" fill="#53fc18"/><rect x="5" y="9" width="1" height="2" fill="#53fc18"/><rect x="5" y="11" width="4" height="1" fill="#53fc18"/>
 <rect x="7" y="11" width="10" height="9" fill="#6d28d9"/>
 <rect x="9" y="13" width="1" height="3" fill="#53fc18"/><rect x="10" y="15" width="1" height="2" fill="#53fc18"/><rect x="11" y="14" width="1" height="2" fill="#53fc18"/><rect x="12" y="15" width="1" height="2" fill="#53fc18"/><rect x="13" y="13" width="1" height="3" fill="#53fc18"/>
 <rect x="5" y="12" width="2" height="6" fill="#6d28d9"/><rect x="5" y="18" width="2" height="2" fill="#f4c9a4"/>
 <g class="arm"><rect x="17" y="12" width="2" height="5" fill="#6d28d9"/><rect x="17" y="17" width="3" height="2" fill="#f4c9a4"/><rect x="19" y="12" width="4" height="7" fill="#14161a"/><rect class="scr" x="20" y="13" width="2" height="5" fill="#53fc18"/></g>
</g>
<g class="leg l"><rect x="8" y="20" width="3" height="5" fill="#1b1d24"/><g class="shin"><rect x="8" y="25" width="3" height="4" fill="#1b1d24"/><rect x="7" y="29" width="5" height="2" fill="#53fc18"/></g></g>
<g class="leg r"><rect x="13" y="20" width="3" height="5" fill="#1b1d24"/><g class="shin"><rect x="13" y="25" width="3" height="4" fill="#1b1d24"/><rect x="12" y="29" width="5" height="2" fill="#53fc18"/></g></g>
<g class="bub"><rect x="18" y="-7" width="9" height="5" fill="#fff"/><rect x="19" y="-2" width="1" height="1" fill="#fff"/><rect class="dot" x="19.5" y="-5" width="1.5" height="1.5" fill="#14161a"/><rect class="dot" x="21.8" y="-5" width="1.5" height="1.5" fill="#14161a"/><rect class="dot" x="24.1" y="-5" width="1.5" height="1.5" fill="#14161a"/></g>
</svg>`;
  function logo() { document.querySelectorAll('.brand .lg').forEach(el => { if (!el.firstChild) el.innerHTML = LOGO; }); }

  /* motion: static blocks fade up when they scroll into view, the rail compacts, the spotlight follows the mouse */
  function motion() {
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const rv = document.querySelectorAll('.sechead, .step, .box, .faq details, .band .in, .term, .steps > div, .tbl, .preview .pv, .form');
    if (!reduce && 'IntersectionObserver' in window) {
      let i = 0; rv.forEach(el => { el.classList.add('rv'); el.style.setProperty('--i', String(i++ % 6)); });
      const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }), {rootMargin: '0px 0px -8% 0px', threshold: 0.08});
      rv.forEach(el => io.observe(el));
      // safety net: anything the observer missed shows up anyway after a moment
      setTimeout(() => rv.forEach(el => { if (el.getBoundingClientRect().top < innerHeight * 1.2) el.classList.add('in'); }), 1500);
      setTimeout(() => rv.forEach(el => el.classList.add('in')), 8000);
    } else rv.forEach(el => el.classList.add('in'));
    const rail = $('.rail');
    if (rail) { const f = () => rail.classList.toggle('scrolled', window.scrollY > 24); f(); addEventListener('scroll', f, {passive: true}); }
    const spot = $('.spot');
    if (spot && !reduce) {
      let x = 0, y = 0, raf = 0;
      addEventListener('mousemove', e => {
        x = e.clientX; y = e.clientY; document.body.classList.add('hasmouse');
        if (!raf) raf = requestAnimationFrame(() => { raf = 0; spot.style.setProperty('--mx', x + 'px'); spot.style.setProperty('--my', y + 'px'); });
      }, {passive: true});
    }
  }

  /* phone: a bottom tab bar on every page, so a thumb can get anywhere in one tap */
  const TAB_ICONS = {
    wall: '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="8" height="8" rx="2"/><rect x="13" y="3" width="8" height="8" rx="2"/><rect x="3" y="13" width="8" height="8" rx="2"/><rect x="13" y="13" width="8" height="8" rx="2"/></svg>',
    clips: '<svg viewBox="0 0 24 24"><path d="M5 4.5v15l13-7.5z"/></svg>',
    cuts: '<svg viewBox="0 0 24 24"><circle cx="6.5" cy="6.5" r="2.6"/><circle cx="6.5" cy="17.5" r="2.6"/><path d="M8.6 8.4 21 19M8.6 15.6 21 5" stroke-width="2.4" stroke-linecap="round" fill="none"/></svg>',
    fees: '<svg viewBox="0 0 24 24"><ellipse cx="12" cy="6.5" rx="8" ry="3.2"/><path d="M4 6.5v5c0 1.8 3.6 3.2 8 3.2s8-1.4 8-3.2v-5" opacity=".7"/><path d="M4 11.5v5c0 1.8 3.6 3.2 8 3.2s8-1.4 8-3.2v-5" opacity=".45"/></svg>',
    launch: '<svg viewBox="0 0 24 24"><path d="M12 3v18M3 12h18" stroke-width="3.2" stroke-linecap="round" fill="none"/></svg>',
  };
  function tabbar() {
    if (document.querySelector('.tabbar')) return;
    const page = (location.pathname.split('/').pop() || 'index.html').toLowerCase();
    const items = [['index.html#board', 'board', 'Wall', 'wall'], ['index.html#week', 'week', 'Clips', 'clips'],
                   ['cuts.html', '', 'Cuts', 'cuts'], ['fees.html', '', 'Fees', 'fees'], ['launch.html', '', 'Launch', 'launch']];
    const nav = document.createElement('nav'); nav.className = 'tabbar'; nav.setAttribute('aria-label', 'sections');
    nav.innerHTML = items.map(([href, sec, label, icon]) => {
      const on = sec ? (page === 'index.html' && sec === 'board') : page === href;
      return `<a href="${href}" data-sec="${sec}" class="${icon === 'launch' ? 'go' : ''}${on ? ' on' : ''}">${TAB_ICONS[icon]}<span>${label}</span></a>`;
    }).join('');
    document.body.appendChild(nav);
    if (page === 'index.html' && 'IntersectionObserver' in window) {
      // the lit tab follows the section under the reader's thumb
      const secs = {board: $('#board'), week: $('#week')};
      const io = new IntersectionObserver(es => {
        es.forEach(e => { if (!e.isIntersecting) return;
          const id = e.target.id; const on = Object.keys(secs).includes(id) ? id : 'board';
          nav.querySelectorAll('a[data-sec]').forEach(a => a.classList.toggle('on', a.dataset.sec === on)); });
      }, {rootMargin: '-40% 0px -50% 0px'});
      Object.values(secs).forEach(el => el && io.observe(el));
    }
  }

  /* depth: cards tilt toward the pointer, a glare slides over them. pointer devices only; phones get the coin and the carousels */
  function tilt() {
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce || !window.matchMedia || !window.matchMedia('(hover:hover) and (pointer:fine)').matches) return;
    document.documentElement.classList.add('tilt');
    const SEL = '.hcard, .tcard, #wall > .card, .fresh .card, .step, .box, .stat';
    let cur = null, raf = 0, ev = null;
    const apply = () => {
      raf = 0; if (!cur || !ev) return;
      const r = cur.getBoundingClientRect(); if (!r.width) return;
      const px = (ev.clientX - r.left) / r.width, py = (ev.clientY - r.top) / r.height;
      const max = cur.classList.contains('hcard') ? 7 : 5;
      cur.style.setProperty('--ry', ((px - .5) * 2 * max).toFixed(2) + 'deg');
      cur.style.setProperty('--rx', ((.5 - py) * 2 * max).toFixed(2) + 'deg');
      cur.style.setProperty('--gx', (px * 100).toFixed(1) + '%'); cur.style.setProperty('--gy', (py * 100).toFixed(1) + '%');
    };
    const clear = el => { if (!el) return; el.style.removeProperty('--rx'); el.style.removeProperty('--ry'); el.classList.remove('tilting'); };
    document.addEventListener('pointermove', e => {
      const el = e.target.closest ? e.target.closest(SEL) : null;
      if (el !== cur) { clear(cur); cur = el; if (cur) cur.classList.add('tilting'); }
      ev = e; if (cur && !raf) raf = requestAnimationFrame(apply);
    }, {passive: true});
    document.addEventListener('pointerleave', () => { clear(cur); cur = null; });
    // entrance animations hold their last keyframe (fill-mode both); release it so the tilt can take the transform
    document.addEventListener('animationend', e => { if (e.target.matches && e.target.matches(SEL)) e.target.style.animation = 'none'; });
  }

  /* the coin: every stream gets one. a css 3d coin with the stream's face, spinning next to the hero card */
  function coin3d(s) {
    const tk = s.coin ? '$' + s.coin.ticker : 'NO COIN YET';
    const ring = `${esc(tk)} · ${esc((s.name || s.slug).toUpperCase())} · WALL · `;
    const face = s.avatar || s.thumb_alt || '';
    const id = 'ring-' + (s.key || 'x').replace(/[^a-z0-9]/gi, '');
    const edge = Array.from({length: 9}, (_, i) => `<i style="--z:${i - 4}"></i>`).join('');
    return `<div class="coin3d ${s.coin ? 'has' : ''}" aria-hidden="true"><div class="c">
  <div class="f front">${face ? `<img src="${esc(face)}" alt="" onerror="this.remove()">` : ''}<b>${esc((s.name || s.slug).slice(0, 1).toUpperCase())}</b>
    <svg viewBox="0 0 100 100"><defs><path id="${id}" d="M50,50 m-39,0 a39,39 0 1,1 78,0 a39,39 0 1,1 -78,0"/></defs><text><textPath href="#${id}">${ring}${ring}</textPath></text></svg></div>
  <div class="f back"><span class="wm" data-t="WALL">WALL</span><small>${s.coin ? 'launched from the wall' : 'launch a coin'}</small></div>
  ${edge}
</div></div>`;
  }

  function clock() {
    logo(); tabbar();
    document.querySelectorAll('.clock').forEach(el => {
      const t = () => { el.textContent = new Date().toISOString().slice(11, 19) + ' UTC'; };
      t(); setInterval(t, 1000);
    });
    document.querySelectorAll('a.xlink').forEach(a => { a.href = X_URL; });
    if (!clock.done) { clock.done = true; motion(); tilt(); }
  }

  function health(state) {
    const el = $('#agent'); if (!el) return;
    if (DEMO && !SAMPLE) { el.className = 'chip stale'; el.innerHTML = '<i></i>demo data'; return; }
    const age = Date.now()/1000 - (state && state.updated || 0);
    const live = SAMPLE || age < 15;
    el.className = 'chip ' + (live ? 'live' : age < 120 ? 'stale' : 'off');
    el.innerHTML = `<i></i>${live ? 'agent live' : age < 120 ? 'agent lagging' : 'agent down'}`;
  }

  function spark(h, line) {
    h = (h || []).slice(-12); if (!h.length) return '<div class="spark"></div>';
    const l = line == null ? null : Math.max(line, 2);  // the floor: under 2 msg/s nothing is ever a spike
    const max = Math.max(l || 0, ...h, 0.5);
    return `<div class="spark" title="rate per tick, last ${h.length} ticks">` + h.map(v =>
      `<i class="${l && v >= l ? 'sp' : l && v >= l / 2 ? 'hi' : ''}" style="height:${Math.max(7, Math.round(100 * v / max))}%"></i>`).join('') + '</div>';
  }

  function card(s, rank) {
    const st = s.live === false ? 'OFF' : (s.state || 'WARMING');
    const pct = Math.min(100, Math.round(100 * (s.rate_live || 0) / Math.max(s.line || 1, 0.1)));
    const coin = s.coin, clip = s.clip;
    const plat = s.platform === 'kick' ? 'kick' : 'twitch';
    const mc = coin && mcaps[coin.mint];
    return `
<article class="card ${st === 'SPIKE' ? 'spike' : st === 'WARM' ? 'warm' : ''}" data-key="${esc(s.key)}" data-name="${esc(s.name || s.slug)}">
  <a class="thumb" href="${esc(s.url)}" target="_blank" rel="noopener">
    ${pic([s.thumb, s.thumb_alt, s.avatar], s.platform, s.name || s.slug, '', false)}
    ${rank ? `<span class="rank">#${String(rank).padStart(2, '0')}</span>` : ''}
    <span class="chip plat ${plat}"><i></i>${plat}</span>
    <span class="badge state ${st}">${st === 'WARMING' ? 'warming ' + (s.watched_s || 0) + 's' : st === 'OFF' ? 'offline' : st}</span>
    ${clip ? `<span class="clipped">cut ${ago(clip.ts)} ago · ${clip.multiplier}x</span>` : ''}
  </a>
  <div class="body">
    <div class="name"><b title="${esc(s.title)}">${esc(s.name || s.slug)}</b><span>${s.live === false ? 'offline' : fmt(s.viewers || 0) + ' watching'}</span></div>
    <div class="title">${esc(s.category ? s.category + ' · ' : '')}${esc(s.title || '')}</div>
    <div class="rate">
      <div>
        <div class="big num">${(s.rate_live || 0).toFixed(1)}<small>msg/s</small></div>
        <div class="meta num"><span>line <b>${s.line}</b>/s</span><span>now <b class="${st==='SPIKE'?'k':st==='WARM'?'a':''}">${s.multiplier}x</b></span><span>peak ${s.peak_rate}/s</span></div>
      </div>
      ${spark(s.history, st === 'WARMING' || st === 'OFF' ? null : s.line)}
    </div>
    <div class="bar ${st.toLowerCase()}"><b style="width:${pct}%"></b><i style="left:100%"></i></div>
    <div class="coin">
      ${coin ? `<span><span class="tk">$${esc(coin.ticker)}</span> <span class="mc" data-mint="${esc(coin.mint)}">${mc ? 'mcap ' + usd(mc.mcap) : 'on this stream'}</span></span>
               <a class="btn ghost sm" href="coin.html?mint=${esc(coin.mint)}">Coin page</a>`
             : `<span class="d">no coin here yet</span>
               <a class="btn sm" href="launch.html?stream=${encodeURIComponent(s.key)}">Launch a coin</a>`}
    </div>
  </div>
</article>`;
  }

  // a stat only re-renders when its text changes, and flashes green when it does
  const set = (id, v) => { const e = $(id); if (!e) return; v = String(v); if (e.textContent === v) return; e.textContent = v; if (e.dataset.seen) { e.classList.remove('tick'); void e.offsetWidth; e.classList.add('tick'); } e.dataset.seen = '1'; };
  function stats(state, clips, fees) {
    const streams = state.streams || [];
    const live = streams.filter(s => s.live !== false);
    set('#st-streams', live.length);
    set('#st-viewers', fmt(live.reduce((a, s) => a + (s.viewers || 0), 0)));
    set('#st-spikes', streams.filter(s => s.state === 'SPIKE').length);
    set('#st-loudest', (Math.max(0, ...streams.map(s => s.rate_live || 0))).toFixed(1) + '/s');
    set('#st-coins', streams.filter(s => s.coin).length);
    if (clips) set('#st-cuts', clips.filter(c => c.ts > Date.now()/1000 - 86400).length);
    if (fees) set('#st-burned', tok(fees.reduce((a, r) => a + ((r.burn && r.burn.tokens_raw) || 0), 0)));
  }

  function renderWall(state) {
    const el = $('#wall'); if (!el) return;
    const streams = (state.streams || []);
    if (!streams.length) { el.innerHTML = `<div class="empty">the agent is joining chats. the first verdicts land 70 seconds after a stream is picked.</div>`; return; }
    const html = streams.map((s, i) => card(s, i + 1));
    const keys = streams.map(s => s.key).join('|');
    const prev = el._src || {}, src = {}; streams.forEach((s, i) => { src[s.key] = html[i]; }); el._src = src;
    if (el.dataset.keys !== keys) { el.dataset.keys = keys; el.innerHTML = html.join(''); }
    else el.querySelectorAll(':scope > .card').forEach(art => {
      const k = art.dataset.key; if (prev[k] === src[k]) return;
      const t = document.createElement('template'); t.innerHTML = src[k].trim();
      const fresh = t.content.firstElementChild;
      // the green ring only when the verdict changes or a clip lands, not on every number tick
      const was = art.querySelector('.badge.state'), now = fresh.querySelector('.badge.state');
      const flash = (was && was.className) !== (now && now.className) || !!art.querySelector('.clipped') !== !!fresh.querySelector('.clipped');
      morphCard(art, fresh, flash);
    });
    const tk = $('#ticker-track');
    if (tk) {
      const live = streams.filter(s => s.live !== false);
      const t = live.map(s => `<span><i class="${s.state === 'SPIKE' ? 'on' : s.state === 'WARM' ? 'warm' : ''}"></i><b>${esc(s.name || s.slug)}</b> ${fmt(s.viewers || 0)} watching${s.state === 'SPIKE' ? ' · <b class="k">over the line</b>' : ''}${s.coin ? ' · $' + esc(s.coin.ticker) : ''}</span>`).join('')
        + `<span>${state.rule ? 'the rule · ' + state.rule.spike_x + 'x the stream’s own last minute · floor ' + state.rule.floor_per_s + '/s · cooldown ' + state.rule.cooldown_s + 's' : 'one rule for every stream'}</span>`;
      if (tk.dataset.t !== t) { tk.dataset.t = t; tk.innerHTML = t + t; tk.parentElement.classList.add('has'); }
    }
  }


  /* hero stage: the loudest stream right now, as one big card with a chat overlay */
  const FX = {canvas: null, ctx: null, lines: [], rate: 3, last: 0, acc: 0, raf: 0, key: null};
  const NAMES = ['xqcL', 'moist_', 'kai_fan', 'dre', 'nova', 'tyler1', 'pogu', 'bbno', 'yungg', 'jsteve', 'clip_it', 'mira', 'oof', 'tt_pay', 'kekw', 'gg_ez', 'zed', 'ali', 'ramen', 'sol_dev'];
  const MSGS = ['W', 'LMAOOO', 'CLIP IT', 'NO WAY', 'HE SAID IT', '???', 'PAUSE', 'chat is cooked', 'W STREAM', 'KEKW', 'bro', 'LOL', 'actually insane', 'cut that', 'WAIT', 'again again', 'nah', 'hes done', 'top 1', 'L', 'omg', 'this is it', 'send it', 'LETS GO', 'cant be real', 'LULW', 'POG', 'he knows', 'real', 'lmao what'];
  const COLORS = ['#53fc18', '#a970ff', '#ff4d5e', '#ffb84a', '#4fd1ff', '#ff7ad9', '#f3f5f1'];
  function fxStart(canvas, rate) {
    fxStop();
    FX.canvas = canvas; FX.ctx = canvas.getContext('2d'); FX.rate = rate; FX.lines = []; FX.acc = 0; FX.last = performance.now();
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    for (let i = 0; i < 12; i++) FX.lines.push(fxLine());
    const tick = now => {
      const dt = Math.min(0.1, (now - FX.last) / 1000); FX.last = now;
      const perSec = Math.min(28, Math.max(0.6, FX.rate));
      FX.acc += dt * perSec;
      while (FX.acc >= 1) { FX.acc -= 1; FX.lines.push(fxLine()); if (FX.lines.length > 40) FX.lines.shift(); }
      fxDraw();
      if (!reduce) FX.raf = requestAnimationFrame(tick);
    };
    FX.raf = requestAnimationFrame(tick);
  }
  function fxStop() { if (FX.raf) cancelAnimationFrame(FX.raf); FX.raf = 0; }
  function fxLine() {
    const n = NAMES[Math.floor(Math.random() * NAMES.length)], m = MSGS[Math.floor(Math.random() * MSGS.length)];
    return {n, m, c: COLORS[Math.floor(Math.random() * COLORS.length)], t: performance.now()};
  }
  function fxDraw() {
    const c = FX.canvas, ctx = FX.ctx; if (!c || !ctx) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = c.clientWidth, h = c.clientHeight;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const colW = Math.min(200, Math.max(140, w * 0.32)), x0 = w - colW - 12, lh = 17;
    // dark column behind the chat so it reads over any frame
    const g = ctx.createLinearGradient(x0 - 40, 0, w, 0); g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(0.35, 'rgba(0,0,0,.45)'); g.addColorStop(1, 'rgba(0,0,0,.72)');
    ctx.fillStyle = g; ctx.fillRect(x0 - 40, 0, colW + 52, h);
    ctx.font = '600 11px "IBM Plex Mono", monospace'; ctx.textBaseline = 'alphabetic';
    const n = FX.lines.length, bottom = h - 14;
    for (let i = 0; i < n; i++) {
      const L = FX.lines[n - 1 - i], y = bottom - i * lh; if (y < 0) break;
      const a = Math.min(1, (y - 10) / (h * 0.35)); if (a <= 0) continue;
      ctx.globalAlpha = a;
      ctx.fillStyle = L.c; ctx.fillText(L.n + ':', x0, y);
      const nw = ctx.measureText(L.n + ':').width;
      ctx.fillStyle = '#f3f5f1'; ctx.fillText(L.m, x0 + nw + 6, y);
    }
    ctx.globalAlpha = 1;
  }
  function renderStage(state) {
    const el = $('#stage'); if (!el) return;
    const streams = (state.streams || []).filter(s => s.live !== false);
    // the hero shows whoever is over the line; otherwise the most-watched stream on the wall
    const s = streams.find(x => x.state === 'SPIKE') || streams.slice().sort((a, b) => (b.viewers || 0) - (a.viewers || 0))[0];
    if (!s) { el.innerHTML = `<div class="glow"></div><div class="empty">the agent is joining chats</div>`; return; }
    const st = s.state || 'WARMING', plat = s.platform === 'kick' ? 'kick' : 'twitch';
    const pct = Math.min(100, Math.round(100 * (s.rate_live || 0) / Math.max(s.line || 1, 0.1)));
    if (FX.key !== s.key) {
      FX.key = s.key;
      el.innerHTML = `<div class="glow"></div>
<div class="float">${coin3d(s)}<article class="hcard" data-name="${esc(s.name || s.slug)}">
  <a class="frame" href="${esc(s.url)}" target="_blank" rel="noopener">
    ${pic([s.thumb, s.thumb_alt, s.avatar], s.platform, s.name || s.slug, '', false)}
    <canvas id="chatfx"></canvas>
    <span class="chip plat ${plat}"><i></i>${plat}</span>
  </a>
  <div class="hbody">
    <div class="name"><b>${esc(s.name || s.slug)}<span class="livetag"><i></i>live · <span data-f="viewers">${fmt(s.viewers || 0)}</span> watching</span></b><span>${esc(s.category || '')}</span></div>
    <div class="title">${esc(s.title || '')}</div>
    <div class="bar ${st.toLowerCase()}" data-f="bar"><b style="width:${pct}%"></b><i style="left:100%"></i></div>
  </div>
  <div class="widget">
    <div class="l"><span>chat right now</span><span class="${st === 'SPIKE' ? 'k' : st === 'WARM' ? 'a' : 'd'}" data-f="state">${st === 'WARMING' ? 'warming up' : st}</span></div>
    <div class="big" data-f="rate">${(s.rate_live || 0).toFixed(1)}<small>msg/s</small></div>
    <div class="m">the line <b data-f="line">${s.line}/s</b> · now <b data-f="mult">${s.multiplier}x</b></div>
    <div data-f="spark">${spark(s.history, st === 'WARMING' ? null : s.line)}</div>
  </div>
</article></div>
<div class="pill">
  ${s.coin ? `<span class="tk">$${esc(s.coin.ticker)}</span><span class="sep"></span>` : ''}
  <span class="st ${st === 'SPIKE' || st === 'WARM' ? '' : 'off'}" data-f="pst"><i></i>${st === 'SPIKE' ? 'over the line' : st === 'WARM' ? 'warming' : 'live'}</span>
  <span class="sep"></span><span class="d" data-f="pv">${fmt(s.viewers || 0)} watching</span>
</div>`;
      fxStart($('#chatfx'), s.rate_live || 0);
    } else {
      FX.rate = s.rate_live || 0;
      const set = (f, v) => { const e = el.querySelector(`[data-f="${f}"]`); if (e) e.innerHTML = v; };
      set('viewers', fmt(s.viewers || 0)); set('pv', fmt(s.viewers || 0) + ' watching');
      set('rate', (s.rate_live || 0).toFixed(1) + '<small>msg/s</small>'); set('line', s.line + '/s'); set('mult', s.multiplier + 'x');
      set('state', st === 'WARMING' ? 'warming up' : st); set('spark', spark(s.history, st === 'WARMING' ? null : s.line));
      const b = el.querySelector('[data-f="bar"]'); if (b) { b.className = 'bar ' + st.toLowerCase(); b.firstElementChild.style.width = pct + '%'; }
    }
  }

  function renderLive(state) {
    const el = $('#live'); if (!el) return;
    const streams = (state.streams || []).filter(s => s.live !== false).slice(0, 7);
    const over = streams.filter(s => s.state === 'SPIKE').length;
    el.innerHTML = `<div class="head"><i class="chip live" style="padding:0;border:0"><i></i></i><span><b>live</b> · the wall, right now</span><span class="n">${over} over the line</span></div>` +
      (streams.length ? streams.map(s => `
<div class="row">
  <span class="dot ${esc(s.platform)}"></span>
  <div class="who"><b>${esc(s.name || s.slug)}</b><span>${esc(s.title || '')}${s.coin ? ' · $' + esc(s.coin.ticker) : ''}</span></div>
  ${spark(s.history, s.state === 'WARMING' ? null : s.line)}
  <div class="r num">${(s.rate_live || 0).toFixed(1)}<small>${s.state === 'SPIKE' ? '<span class="k">' + s.multiplier + 'x · SPIKE</span>' : s.state === 'WARM' ? '<span class="a">' + s.multiplier + 'x</span>' : s.state === 'WARMING' ? 'warming' : s.multiplier + 'x'}</small></div>
</div>`).join('') : `<div class="row"><span></span><div class="who"><b class="d">joining chats…</b></div></div>`) +
      `<div class="foot"><span>bars: rate per 10 s tick, green over the line</span><span>updated ${state.updated ? ago(state.updated) + ' ago' : '–'}</span></div>`;
  }

  function clipCard(c, feat) {
    return `
<article class="card ${feat ? 'feat' : ''}" data-name="${esc(c.name || c.slug)}">
  <a class="thumb" href="${clipUrl(c.file)}" target="_blank" rel="noopener">
    ${pic([c.thumb], c.platform, c.name || c.slug)}
    <span class="play"><i></i></span>
    <span class="chip plat ${esc(c.platform)}"><i></i>${esc(c.platform)}</span>
    <span class="clipped">${c.multiplier}x · ${c.rate}/s</span>
  </a>
  <div class="body">
    <div class="name"><b>${esc(c.name || c.slug)}</b><span>${ago(c.ts)} ago · ${c.duration_s}s</span></div>
    <div class="cutmeta"><span>chat <b>${c.baseline}/s → ${c.rate}/s</b></span><span>${fmt(c.viewers)} watching</span>${feat ? `<span>${esc(c.title || '')}</span>` : ''}</div>
    ${feat ? `<div class="coin"><span class="d">${c.posted ? 'posted from the coin account' : 'in the queue, a person posts it'}</span><a class="btn ghost sm" href="${clipUrl(c.file)}" download>Download</a></div>` : ''}
  </div>
</article>`;
  }

  async function renderFresh() {
    const el = $('#fresh'); if (!el) return [];
    let clips = [];
    try { clips = await get('clips.json'); } catch (e) {}
    const recent = clips.slice(-5).reverse();
    if (!recent.length) { el.innerHTML = `<div class="empty">no cuts yet. the first clip appears the first time a chat crosses the line.</div>`; return clips; }
    el.innerHTML = `<div>${clipCard(recent[0], true)}</div><div class="side">${recent.slice(1).map(c => clipCard(c)).join('')}</div>`;
    return clips;
  }

  async function renderClips(all) {
    const el = $('#clips'); if (!el) return;
    let clips = [];
    try { clips = await get('clips.json'); } catch (e) {}
    clips = (all ? clips : clips.slice(-12)).reverse();
    el.innerHTML = clips.length ? clips.map(c => clipCard(c)).join('') : `<div class="empty">no cuts yet. the first clip appears the first time a chat crosses the line.</div>`;
  }

  /* the week's loudest clips across the wall, from the platforms themselves */
  const dur = s => s >= 60 ? Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0') : '0:' + String(s).padStart(2, '0');
  let coinsByStream = {};
  function trendCard(c, i) {
    const coin = coinsByStream[c.key];
    return `
<article class="tcard" data-name="${esc(c.name || c.slug)}">
  <a class="thumb" href="${esc(c.url)}" target="_blank" rel="noopener" title="${esc(c.title)}">
    ${pic([c.thumb, c.avatar], c.platform, c.name || c.slug)}
    <span class="rank">#${String(i + 1).padStart(2, '0')}</span>
    <span class="views">▶ ${fmt(c.views)}</span>
    <span class="dur">${dur(c.duration_s || 0)}</span>
    <span class="chip plat ${esc(c.platform)}"><i></i>${esc(c.platform)}</span>
    <span class="play"><i></i></span>
  </a>
  <div class="body">
    <div class="who">${c.avatar ? `<img class="av" src="${esc(c.avatar)}" alt="" loading="lazy" onerror="this.remove()">` : ''}<b>${esc(c.name || c.slug)}</b><span>${esc(c.category || '')}${c.created ? ' · ' + ago(Date.parse(c.created) / 1000) + ' ago' : ''}</span></div>
    <div class="title" title="${esc(c.title)}">${esc(c.title || 'untitled clip')}</div>
    <div class="act">
      <a class="btn sm" href="${esc(c.url)}" target="_blank" rel="noopener">Watch</a>
      ${coin ? `<a class="btn ghost sm" href="coin.html?mint=${esc(coin.mint)}">$${esc(coin.ticker)}</a>`
             : `<a class="btn fill sm" href="launch.html?stream=${encodeURIComponent(c.key)}">Launch a coin</a>`}
    </div>
  </div>
</article>`;
  }
  async function renderTrending() {
    const el = $('#trending'); if (!el) return;
    let doc = null;
    try { doc = await get('trending.json'); } catch (e) {}
    try { for (const c of await get('coins.json')) coinsByStream[c.stream] = coinsByStream[c.stream] || c; } catch (e) {}
    const clips = (doc && doc.clips) || [];
    if (!clips.length) { el.innerHTML = `<div class="empty">the week's clips are loading on the agent's side. check back in a minute.</div>`; return; }
    el.innerHTML = clips.slice(0, 12).map(trendCard).join('');
  }

  async function renderSpikes(n) {
    const el = $('#spikes'); if (!el) return;
    let sp = [];
    try { sp = await get('spikes.json'); } catch (e) {}
    sp = sp.slice(-(n || 10)).reverse();
    el.innerHTML = `<span class="c"># spikes.json · every line is a verdict from the rule</span>\n` + (sp.length ? sp.map(s => `<span class="p">${new Date(s.ts*1000).toISOString().slice(11,19)}</span>  ${(s.platform+':'+s.slug).padEnd(26)} ${String(s.rate).padStart(6)}/s  base ${String(s.baseline).padStart(5)}/s  <span class="k">${s.multiplier}x</span>  ${fmt(s.viewers)} watching`).join('\n')
      : `<span class="c"># nothing crossed the line yet.</span>`);
  }

  async function renderBurn(launch) {
    const el = $('#burn'); if (!el) return null;
    let rounds = [];
    try { rounds = await get('fees.json'); } catch (e) {}
    const collected = rounds.reduce((a, r) => a + (r.collected || 0), 0);
    const spent = rounds.reduce((a, r) => a + ((r.burn && r.burn.burn_sig && r.burn.lamports) || 0), 0);
    const burned = rounds.reduce((a, r) => a + ((r.burn && r.burn.tokens_raw) || 0), 0);
    const held = rounds.reduce((a, r) => a + ((r.burn && r.burn.held && r.burn.lamports) || 0), 0);
    const paid = rounds.reduce((a, r) => a + (r.launchers || []).filter(x => x.sig).reduce((b, x) => b + x.lamports, 0), 0);
    const last = rounds[rounds.length - 1];
    const next = last ? Math.max(0, last.ts + 3600 - Date.now()/1000) : null;
    const mint = launch && launch.wall_mint;
    el.innerHTML = `
<div class="head"><p class="eyebrow"><b>$WALL</b> · buyback and burn</p><h3>The wall buying back $WALL</h3>
<p>40% of every coin's creator fees buys $WALL on Jupiter once an hour and burns what it bought in the same round. ${mint ? 'Every round has two signatures.' : 'Until the mint exists the share is held in the hot wallet and shown below as waiting.'}</p></div>
<div class="stats">
  <div class="stat"><div class="l">$WALL burned</div><div class="v k num">${tok(burned)}</div></div>
  <div class="stat"><div class="l">sol spent on burns</div><div class="v num">${sol(spent)}</div></div>
  <div class="stat"><div class="l">${held ? 'waiting for the mint' : 'paid to launchers'}</div><div class="v num ${held ? 'a' : 't'}">${sol(held || paid)} SOL</div></div>
  <div class="stat"><div class="l">rounds · collected</div><div class="v num">${rounds.length} · ${sol(collected)}</div></div>
</div>
<div class="rows">${rounds.length ? rounds.slice(-5).reverse().map(r => {
      const b = r.burn || {};
      return `<div class="row"><span class="ago">${ago(r.ts)} ago</span>
        <span class="what">${b.burn_sig ? `<b>${tok(b.tokens_raw)} burned</b>` : b.held ? `<b class="a">${sol(b.lamports)} SOL waiting</b> <span class="d">for the $WALL mint</span>` : b.error ? `<b class="r">burn failed</b> <span class="d">${esc(b.error)}</span>` : '<span class="d">nothing to burn</span>'}
        <span class="d"> · ${sol(r.collected)} SOL collected · launchers ${sol((r.split && r.split.launchers) || 0)}</span></span>
        <span class="sol">${sol(b.lamports || 0)} SOL</span>
        <span class="tx">${b.burn_sig ? tx(b.buy_sig, 'buy') + ' ' + tx(b.burn_sig, 'burn') : tx(r.collect_sig, 'collect')}</span></div>`;
    }).join('') : `<div class="row"><span class="ago">–</span><span class="what d">no rounds yet. the first one runs an hour after the first coin trades.</span></div>`}</div>
<div class="foot">${mint ? `<a href="https://solscan.io/token/${esc(mint)}" target="_blank" rel="noopener">$WALL on Solscan ↗</a> · ` : ''}<a href="fees.html">Full fee log, every signature →</a> · next round in ${next == null ? '–' : Math.ceil(next / 60) + ' min'}</div>`;
    return rounds;
  }

  function renderToken(launch) {
    const el = $('#mint'); if (!el) return;
    const mint = launch && launch.wall_mint;
    el.innerHTML = mint
      ? `<code>${esc(mint)}</code> <a class="btn sm" href="https://pump.fun/coin/${esc(mint)}" target="_blank" rel="noopener">pump.fun</a> <a class="btn ghost sm" href="https://dexscreener.com/solana/${esc(mint)}" target="_blank" rel="noopener">dexscreener</a> <a class="btn ghost sm" href="https://solscan.io/token/${esc(mint)}" target="_blank" rel="noopener">solscan</a>`
      : `<code>mint: posted on the project account on launch day</code> <a class="btn ghost sm xlink2" href="${X_URL}" target="_blank" rel="noopener">follow for the drop</a>`;
    const hold = $('#hold-req'); if (hold) hold.textContent = launch && launch.min_wall_hold ? launch.min_wall_hold + ' $WALL' : 'set on launch day';
  }

  async function marketcaps(coins) {
    if (SAMPLE || !coins || !coins.length) return;
    try {
      const mints = coins.map(c => c.mint).slice(0, 30).join(',');
      const r = await fetch(`https://api.dexscreener.com/tokens/v1/solana/${mints}`);
      if (!r.ok) return;
      for (const p of await r.json()) {
        const m = p.baseToken && p.baseToken.address; if (!m) continue;
        const cur = mcaps[m];
        if (!cur || (p.liquidity && p.liquidity.usd > (cur.liq || 0))) mcaps[m] = {mcap: p.marketCap || p.fdv, vol: p.volume && p.volume.h24, liq: p.liquidity && p.liquidity.usd};
      }
      document.querySelectorAll('.mc[data-mint]').forEach(e => { const v = mcaps[e.dataset.mint]; if (v) e.textContent = 'mcap ' + usd(v.mcap); });
    } catch (e) { /* dexscreener down, cards still render */ }
  }

  /* the agent, right now: its event log as a feed. every line is something the agent did, with the number that caused it */
  const FEED = {seen: 0, first: true};
  function feedLine(e) {
    const who = `<b>${esc(e.name || e.key || '')}</b>`;
    const link = e.key ? `index.html#board` : '';
    switch (e.kind) {
      case 'boot': return {c: 'd', t: 'agent started · joining chats'};
      case 'join': return {c: '', t: `joined the chat of ${who} <span class="d">· ${fmt(e.viewers || 0)} watching${e.live === false ? ' · offline' : ''}</span>`};
      case 'drop': return {c: 'd', t: `left ${who} <span class="d">· ${e.live === false ? 'went offline' : 'off the roster'}</span>`};
      case 'warm': return {c: 'a', t: `${who} is warming up <span class="a">${e.multiplier}x</span> <span class="d">its own last minute · ${e.rate}/s</span>`};
      case 'calm': return {c: 'd', t: `${who} back to quiet <span class="d">· ${e.rate}/s</span>`};
      case 'spike': return {c: 'k', t: `${who} crossed the line <span class="k">${e.multiplier}x</span> <span class="d">· ${e.rate}/s vs ${e.baseline}/s</span>`};
      case 'cut': return {c: 'k', t: `cut ${e.duration_s || 30} s on ${who} <span class="d">· ${e.multiplier}x</span> ${e.file ? `<a href="${clipUrl(e.file)}" target="_blank" rel="noopener">open ↗</a>` : ''}`, thumb: e.thumb};
      case 'launch': return {c: 't', t: `<b class="t">$${esc(e.ticker || '')}</b> launched on <b>${esc(e.stream_name || (e.key || '').split(':')[1] || '')}</b> <span class="d">· by ${esc((e.launcher || '').slice(0, 4))}…${esc((e.launcher || '').slice(-4))}</span> ${e.mint ? `<a href="coin.html?mint=${esc(e.mint)}">coin page</a>` : ''}`};
      case 'fees': return {c: 't', t: `fee round <span class="d">· ${sol(e.collected || 0)} SOL collected · burn ${sol(e.burn || 0)} · launchers ${sol(e.launchers || 0)} · platform ${sol(e.platform || 0)}${e.swept ? ' · swept ' + sol(e.swept) : ''}</span>`};
      case 'trending': return {c: 'd', t: `week's clips refreshed <span class="d">· ${e.n} clips</span>`};
      default: return {c: 'd', t: esc(e.kind)};
    }
  }
  const KIND_ICON = {boot: '●', join: '+', drop: '−', warm: '▲', calm: '▽', spike: '⚡', cut: '✂', launch: '$', fees: '◎', trending: '▶'};
  async function renderFeed() {
    const el = $('#feed'); if (!el) return;
    let doc = null;
    try { doc = await get('events.json'); } catch (e) { if (!el.children.length) el.innerHTML = `<div class="empty">the agent has not written its log yet.</div>`; return; }
    const evs = (doc && doc.events || []).slice(-40).reverse();
    const cnt = $('#feed-n'); if (cnt) { const h = evs.filter(e => Date.now() / 1000 - e.ts < 3600).length; cnt.textContent = innerWidth < 640 ? `${h} / h` : `${h} in the last hour`; }
    if (!evs.length) { el.innerHTML = `<div class="empty">nothing yet. the first lines land when the agent joins its first chat.</div>`; return; }
    const newest = evs[0].ts;
    el.innerHTML = evs.map(e => {
      const l = feedLine(e);
      const fresh = !FEED.first && e.ts > FEED.seen;
      return `<div class="ev ${l.c} ${fresh ? 'new' : ''}" data-ts="${e.ts}"><span class="ic ${esc(e.kind)}">${KIND_ICON[e.kind] || '·'}</span>
        <div class="tx">${l.t}${l.thumb ? `<a class="th" href="${clipUrl(e.file)}" target="_blank" rel="noopener">${pic([l.thumb], e.platform, e.name, 'sm')}</a>` : ''}</div>
        <span class="when num" title="${new Date(e.ts * 1000).toISOString()}">${ago(e.ts)}</span></div>`;
    }).join('');
    FEED.seen = Math.max(FEED.seen, newest); FEED.first = false;
  }

  /* coins on the wall: a leaderboard by market cap, from coins.json plus dexscreener */
  async function renderBoard(state) {
    const el = $('#coins-board'); if (!el) return;
    let coins = [];
    try { coins = await get('coins.json'); } catch (e) {}
    if (!coins.length) {
      el.innerHTML = `<div class="empty board-empty"><b>No coin on the wall yet.</b> The first launcher gets the first fee round to themselves. <a class="btn fill sm" href="launch.html">Launch the first →</a></div>`;
      return;
    }
    await marketcaps(coins);
    const byKey = {}; ((state && state.streams) || []).forEach(s => { byKey[s.key] = s; });
    const rows = coins.slice().sort((a, b) => ((mcaps[b.mint] || {}).mcap || 0) - ((mcaps[a.mint] || {}).mcap || 0)).slice(0, 10);
    el.innerHTML = `<table class="board"><thead><tr><th>#</th><th>coin</th><th>stream</th><th class="r">mcap</th><th class="r">vol 24h</th><th class="r">room</th><th class="r">age</th></tr></thead><tbody>` +
      rows.map((c, i) => { const m = mcaps[c.mint] || {}; const s = byKey[c.stream];
        const room = !s ? '<span class="d">off the wall</span>' : s.live === false ? '<span class="d">offline</span>' : s.state === 'SPIKE' ? `<span class="k">${s.multiplier}x spike</span>` : s.state === 'WARM' ? `<span class="a">${s.multiplier}x warm</span>` : `<span class="d">${s.rate_live || 0}/s quiet</span>`;
        return `<tr><td class="num d">${String(i + 1).padStart(2, '0')}</td>
          <td><a class="tk" href="coin.html?mint=${esc(c.mint)}">$${esc(c.ticker)}</a> <span class="d">${esc(c.name)}</span></td>
          <td><span class="dot ${esc((c.stream || '').split(':')[0])}"></span>${esc(s ? (s.name || s.slug) : (c.stream || '').split(':')[1] || '')}</td>
          <td class="num r">${m.mcap ? usd(m.mcap) : '–'}</td><td class="num r">${m.vol ? usd(m.vol) : '–'}</td>
          <td class="num r">${room}</td><td class="num r d">${ago(c.created)}</td></tr>`; }).join('') + `</tbody></table>`;
  }

  /* toasts: a crossing or a cut anywhere on the wall shows up wherever the reader is on the page */
  const TOAST = {state: {}, clips: 0, ready: false};
  function toast(html, cls) {
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let box = $('.toasts'); if (!box) { box = document.createElement('div'); box.className = 'toasts'; document.body.appendChild(box); }
    const t = document.createElement('div'); t.className = 'toast ' + (cls || ''); t.innerHTML = html;
    box.prepend(t); while (box.children.length > 3) box.lastElementChild.remove();
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), reduce ? 0 : 500); }, 7000);
  }
  function watchSpikes(state) {
    const over = (state.streams || []).some(s => s.state === 'SPIKE');
    document.body.classList.toggle('overline', over);
    (state.streams || []).forEach(s => {
      const prev = TOAST.state[s.key]; TOAST.state[s.key] = s.state;
      if (TOAST.ready && prev && prev !== 'SPIKE' && s.state === 'SPIKE')
        toast(`<span class="ic">⚡</span><div><b>${esc(s.name || s.slug)}</b> crossed the line <b class="k">${s.multiplier}x</b><br><span class="d">${s.rate}/s against ${s.baseline}/s · the agent is cutting</span></div>`, 'spike');
    });
    TOAST.ready = true;
  }
  function watchCuts(clips) {
    const n = (clips || []).length;
    if (TOAST.clips && n > TOAST.clips) {
      const c = clips[n - 1];
      toast(`${pic([c.thumb], c.platform, c.name, 'th')}<div><b>cut 30 s on ${esc(c.name || c.slug)}</b> · ${c.multiplier}x<br><a href="${clipUrl(c.file)}" target="_blank" rel="noopener">open the clip ↗</a> <span class="d">· <a href="cuts.html">all cuts</a></span></div>`, 'cut');
    }
    TOAST.clips = n;
  }

  /* numbers roll in the first time they appear */
  function odometer(root) {
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    (root || document).querySelectorAll('.hero-stats .v, #coins-board .num').forEach(el => {
      if (el.dataset.rolled || reduce) return;
      const txt = el.textContent.trim(); const m = txt.match(/^([$]?)([\d.,]+)([KMB]?)(.*)$/);
      if (!m || !parseFloat(m[2].replace(/,/g, ''))) return;
      el.dataset.rolled = '1';
      const target = parseFloat(m[2].replace(/,/g, '')), dec = (m[2].split('.')[1] || '').length, t0 = performance.now();
      const step = now => { const k = Math.min(1, (now - t0) / 900), e = 1 - Math.pow(1 - k, 3);
        el.textContent = m[1] + (target * e).toFixed(dec) + m[3] + m[4]; if (k < 1) requestAnimationFrame(step); };
      requestAnimationFrame(step);
    });
  }

  async function loop() {
    try {
      const state = await get('state.json');
      health(state); renderWall(state); renderLive(state); renderStage(state); stats(state); watchSpikes(state);
      if (!loop.n) odometer(); loop.n = (loop.n || 0) + 1;
      if (loop.n % 3 === 1) renderFeed();
      if (loop.n % 15 === 1) renderBoard(state);
      return state;
    } catch (e) {
      health(null);
      const el = $('#wall'); if (el && !el.children.length) el.innerHTML = `<div class="empty">cannot reach the agent at ${esc(API)}. ${esc(e.message)}</div>`;
      const lv = $('#live'); if (lv && !lv.children.length) lv.innerHTML = `<div class="empty">agent unreachable</div>`;
    }
  }

  async function init() {
    clock();
    let launch = null;
    try { launch = await get('launch.json'); } catch (e) {}
    renderToken(launch);
    const state = await loop(); setInterval(loop, 2000);
    const clips = await renderFresh(); watchCuts(clips); setInterval(async () => watchCuts(await renderFresh()), 20000);
    renderSpikes(8); setInterval(() => renderSpikes(8), 20000);
    renderTrending(); setInterval(renderTrending, 600000);
    const fees = await renderBurn(launch); setInterval(() => renderBurn(launch), 60000);
    if (state) stats(state, clips, fees || []);
    try { marketcaps(await get('coins.json')); } catch (e) {}
    setInterval(async () => { try { marketcaps(await get('coins.json')); } catch (e) {} }, 60000);
  }
  return {get API() { return API; }, get DEMO() { return DEMO; }, SAMPLE, X_URL, get, esc, fmt, usd, ago, sol, tok, tx, clipUrl, init, health, clock, logo, motion, tabbar, tilt, coin3d, toast, renderFeed, renderBoard, odometer, card, clipCard, spark, pic, imgFail, renderClips, renderSpikes, renderStage, renderTrending, marketcaps, mcaps};
})();
