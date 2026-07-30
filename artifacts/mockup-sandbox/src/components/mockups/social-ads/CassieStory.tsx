export function CassieStory() {
  return (
    <main className="yonder-story">
      <img
        className="story-image"
        src="/__mockup/images/yonder-desert-highway.png"
        alt="A desert highway winding between red mesas at dusk"
      />
      <div className="story-wash" />
      <div className="story-grain" />

      <header className="story-top">
        <span className="stamp">YONDER</span>
        <span className="edition">ESCAPE FINDER · 01</span>
      </header>

      <section className="story-copy">
        <p className="eyebrow">TYPE A TRIP. GET REAL PRICES.</p>
        <h1>
          Need
          <br />
          Out<span>?</span>
        </h1>
        <div className="rule" />
        <p className="tagline">Buy The Ticket, Take The Ride.</p>
      </section>

      <footer className="story-footer">
        <span className="url">yonder.city</span>
        <span className="direction">↑ FIND YOUR NEXT FLIGHT</span>
      </footer>

      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Playfair+Display:wght@700;800;900&display=swap');
        :root { color-scheme: light; }
        * { box-sizing: border-box; }
        html, body, #root { width: 100%; height: 100%; margin: 0; }
        body { overflow: hidden; background: #e8dcc0; }
        .yonder-story {
          position: relative; width: 100vw; height: 100vh; min-height: 100dvh;
          overflow: hidden; color: #f5eed8; background: #1a1208;
          isolation: isolate;
        }
        .story-image {
          position: absolute; inset: 0; width: 100%; height: 100%;
          object-fit: cover; object-position: center; z-index: -3;
          animation: settle 1.4s ease-out both;
        }
        .story-wash {
          position: absolute; inset: 0; z-index: -2;
          background:
            linear-gradient(180deg, rgba(19,14,8,.52) 0%, rgba(19,14,8,.04) 28%, rgba(19,14,8,.18) 49%, rgba(19,14,8,.82) 83%, rgba(19,14,8,.94) 100%),
            linear-gradient(90deg, rgba(24,18,8,.22), transparent 48%, rgba(24,18,8,.18));
        }
        .story-grain {
          position: absolute; inset: 0; pointer-events: none; opacity: .16; z-index: 3;
          background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 180 180' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.36'/%3E%3C/svg%3E");
          mix-blend-mode: soft-light;
        }
        .story-top, .story-footer {
          position: absolute; left: 7.5%; right: 7.5%; display: flex;
          align-items: center; justify-content: space-between;
          font-family: 'DM Mono', ui-monospace, monospace; font-size: 8px;
          letter-spacing: .15em; text-transform: uppercase;
        }
        .story-top { top: 5.7%; }
        .stamp {
          border: 1px solid rgba(245,238,216,.78); padding: 7px 8px 6px;
          letter-spacing: .22em; font-weight: 500;
        }
        .edition { opacity: .72; }
        .story-copy {
          position: absolute; left: 7.5%; right: 7.5%; bottom: 18.2%;
          animation: rise .9s .15s cubic-bezier(.2,.8,.2,1) both;
        }
        .eyebrow {
          margin: 0 0 15px; font: 500 9px/1.2 'DM Mono', ui-monospace, monospace;
          letter-spacing: .16em; color: #c4dfc4;
        }
        h1 {
          margin: 0; color: #f5eed8; font: 800 clamp(65px, 22vw, 118px)/.83 'Playfair Display', Georgia, serif;
          letter-spacing: -.075em;
        }
        h1 span { color: #a8cfb1; }
        .rule { width: 100%; height: 1px; background: rgba(245,238,216,.62); margin: 22px 0 13px; }
        .tagline {
          margin: 0; font: 500 11px/1.4 'DM Mono', ui-monospace, monospace;
          text-transform: uppercase; letter-spacing: .08em; color: #f5eed8;
        }
        .story-footer { bottom: 5.5%; }
        .url { color: #c4dfc4; font-size: 13px; letter-spacing: .08em; }
        .direction { opacity: .65; font-size: 7px; }
        @keyframes settle { from { transform: scale(1.045); opacity: .2; } to { transform: scale(1); opacity: 1; } }
        @keyframes rise { from { transform: translateY(18px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
        @media (max-width: 360px) {
          .story-copy { bottom: 17%; }
          .story-top, .story-footer { left: 6%; right: 6%; }
        }
      `}</style>
    </main>
  );
}