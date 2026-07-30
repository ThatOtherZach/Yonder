import { useState } from 'react';

export function XPost() {
  const [copied, setCopied] = useState(false);

  const copyUrl = () => {
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };

  return (
    <main
      style={{
        width: '100vw',
        height: '100vh',
        minHeight: '100%',
        overflow: 'hidden',
        position: 'relative',
        color: '#1a1208',
        background: 'linear-gradient(126deg, #f7f0dc 0%, #eee4c9 48%, #e1d2b1 100%)',
        fontFamily: 'Georgia, "Times New Roman", serif',
      }}
    >
      <style>{`
        * { box-sizing: border-box; }
        .xp-shell { height: 100%; position: relative; display: flex; flex-direction: column; }
        .xp-top { height: 15%; min-height: 46px; display:flex; justify-content:space-between; align-items:center; padding: 0 5.1%; border-bottom: 1px solid rgba(26,18,8,.35); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: clamp(8px,1.5vw,12px); letter-spacing:.12em; text-transform:uppercase; }
        .xp-brand { font-weight:800; letter-spacing:.18em; font-size: clamp(12px,2.4vw,18px); }
        .xp-stamp { border: 1px solid #2a6b4f; color:#2a6b4f; padding: 5px 9px; transform: rotate(-3deg); font-weight:700; }
        .xp-main { flex:1; min-height:0; display:grid; grid-template-columns: 49% 51%; align-items:center; padding: 2.1% 5.1% 1.6%; gap:3%; }
        .xp-copy { position:relative; z-index:2; }
        .xp-kicker { display:flex; align-items:center; gap:9px; margin-bottom:3%; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; color:#2a6b4f; font-size:clamp(8px,1.35vw,12px); font-weight:700; letter-spacing:.12em; text-transform:uppercase; }
        .xp-kicker:before { content:''; display:block; width:29px; height:1px; background:#2a6b4f; }
        .xp-title { margin:0; font-size: clamp(48px, 11.7vw, 104px); line-height:.79; letter-spacing:-.075em; font-weight:900; }
        .xp-title em { display:block; color:#2a6b4f; font-style:normal; margin-left: .17em; }
        .xp-rule { width: 70%; height:1px; background:#1a1208; opacity:.5; margin:5% 0 4%; }
        .xp-deck { margin:0; max-width:320px; font-size:clamp(13px,2vw,20px); line-height:1.15; font-weight:700; }
        .xp-deck small { display:block; margin-top:7px; color:#2a6b4f; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:clamp(8px,1.2vw,11px); letter-spacing:.09em; text-transform:uppercase; }
        .xp-url { display:inline-flex; margin-top:7%; align-items:center; gap:10px; background:#2a6b4f; color:#f7f0dc; padding:10px 14px 9px; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:clamp(11px,1.75vw,15px); letter-spacing:.07em; font-weight:800; cursor:pointer; border:0; }
        .xp-url:after { content:'↗'; font-size:1.2em; }
        .xp-url:hover { background:#1e513c; }
        .xp-art { height:100%; min-height:180px; position:relative; display:flex; align-items:center; justify-content:center; }
        .xp-sun { position:absolute; width:32%; aspect-ratio:1; border-radius:50%; background:#d58a4c; top:10%; right:12%; opacity:.72; }
        .xp-road { position:absolute; bottom:2%; left:15%; width:70%; height:51%; background:#5f5039; clip-path:polygon(40% 0,60% 0,100% 100%,0 100%); opacity:.86; }
        .xp-road:after { content:''; position:absolute; top:5%; bottom:0; left:48%; width:4%; background:repeating-linear-gradient(to bottom, #edddae 0 18px, transparent 18px 36px); opacity:.85; }
        .xp-horizon { position:absolute; bottom:43%; left:0; right:0; height:28%; background:#385c52; clip-path:polygon(0 65%, 9% 56%, 17% 65%, 25% 42%, 34% 65%, 44% 50%, 54% 66%, 65% 39%, 75% 62%, 83% 49%, 91% 62%, 100% 40%, 100% 100%, 0 100%); }
        .xp-frame { position:absolute; inset:5% 3% 4% 3%; border:1px solid rgba(26,18,8,.55); pointer-events:none; }
        .xp-frame:before, .xp-frame:after { content:''; position:absolute; width:12px; height:12px; border:1px solid #2a6b4f; }
        .xp-frame:before { left:-1px; top:-1px; border-right:0; border-bottom:0; }
        .xp-frame:after { right:-1px; bottom:-1px; border-left:0; border-top:0; }
        .xp-badge { position:absolute; right:4%; bottom:8%; width:76px; height:76px; border:2px solid #2a6b4f; color:#2a6b4f; border-radius:50%; transform:rotate(13deg); display:flex; justify-content:center; align-items:center; text-align:center; font-family:ui-monospace, monospace; font-size:9px; line-height:1.1; font-weight:800; letter-spacing:.04em; }
        .xp-badge:before { content:''; position:absolute; inset:5px; border:1px dashed #2a6b4f; border-radius:50%; }
        .xp-badge span { position:relative; }
        .xp-bottom { height:15%; min-height:42px; border-top:1px solid rgba(26,18,8,.35); display:flex; align-items:center; justify-content:space-between; padding:0 5.1%; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:clamp(7px,1.25vw,10px); letter-spacing:.08em; text-transform:uppercase; }
        .xp-bottom b { color:#2a6b4f; }
        .xp-perf { height:100%; width:16px; background:radial-gradient(circle at 0 50%, transparent 0 4px, #1a1208 4.5px 5px, transparent 5.5px) repeat-y; background-size:16px 16px; opacity:.62; }
        @media (max-width: 560px) { .xp-main { grid-template-columns: 1fr 1fr; gap:0; padding-right:3%; } .xp-art { transform:scale(1.12); transform-origin:right center; } .xp-badge { width:54px; height:54px; font-size:7px; } .xp-frame { inset:5% 1% 4% 1%; } }
      `}</style>
      <div className="xp-shell">
        <header className="xp-top">
          <span className="xp-brand">YONDER</span>
          <span className="xp-stamp">AIR / LAND / ELSEWHERE</span>
        </header>
        <section className="xp-main">
          <div className="xp-copy">
            <div className="xp-kicker">A public service announcement</div>
            <h1 className="xp-title">Need<em>Out?</em></h1>
            <div className="xp-rule" />
            <p className="xp-deck">
              Type a trip. Get real prices.
              <small>AI-powered escape finder</small>
            </p>
            <button className="xp-url" onClick={copyUrl} aria-label="Visit yonder.city">
              {copied ? 'COPIED' : 'YONDER.CITY'}
            </button>
          </div>
          <div className="xp-art" aria-hidden="true">
            <div className="xp-frame" />
            <div className="xp-sun" />
            <div className="xp-horizon" />
            <div className="xp-road" />
            <div className="xp-badge"><span>GO<br />FARTHER<br />★ 2025 ★</span></div>
          </div>
        </section>
        <footer className="xp-bottom">
          <span><b>YONDER / 001</b> &nbsp; FIND YOUR NEXT FLIGHT</span>
          <span>BUY THE TICKET, TAKE THE RIDE.</span>
          <span className="xp-perf" />
        </footer>
      </div>
    </main>
  );
}