export function AdB() {
  return (
    <main className="ad-b" aria-label="Yonder travel advertisement">
      <style>{`
        .ad-b, .ad-b * { box-sizing: border-box; }
        .ad-b {
          width: 100vw;
          height: 100vh;
          min-height: 100%;
          overflow: hidden;
          position: relative;
          isolation: isolate;
          color: #f5eed8;
          background: #1a1208 url('/__mockup/images/yonder-desert-highway.png') center center / cover no-repeat;
          font-family: 'Playfair Display', Georgia, serif;
        }
        .ad-b::before {
          content: '';
          position: absolute;
          z-index: -1;
          inset: 0;
          background:
            linear-gradient(180deg, rgba(17, 12, 5, .7) 0%, rgba(20, 15, 7, .78) 48%, rgba(13, 10, 5, .9) 100%),
            radial-gradient(ellipse at center, rgba(42, 107, 79, .12), transparent 66%);
        }
        .ad-b::after {
          content: '';
          pointer-events: none;
          position: absolute;
          z-index: 4;
          inset: 0;
          opacity: .18;
          mix-blend-mode: screen;
          background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 180 180' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.82' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.75'/%3E%3C/svg%3E");
        }
        .ad-b__frame {
          position: absolute;
          inset: clamp(16px, 3.1vw, 46px);
          border: 1px solid rgba(245, 238, 216, .38);
          pointer-events: none;
        }
        .ad-b__frame::before, .ad-b__frame::after {
          content: '';
          position: absolute;
          width: 38px;
          height: 38px;
          border-color: #d9be79;
          border-style: solid;
        }
        .ad-b__frame::before { left: -1px; top: -1px; border-width: 1px 0 0 1px; }
        .ad-b__frame::after { right: -1px; bottom: -1px; border-width: 0 1px 1px 0; }
        .ad-b__topline {
          position: absolute;
          top: clamp(28px, 5vw, 74px);
          left: 50%;
          transform: translateX(-50%);
          color: #2a6b4f;
          font: 700 clamp(10px, 1.25vw, 14px)/1 ui-monospace, SFMono-Regular, Menlo, monospace;
          letter-spacing: .19em;
          text-transform: uppercase;
          white-space: nowrap;
        }
        .ad-b__topline::after {
          content: '';
          display: block;
          width: 44px;
          height: 1px;
          margin: 13px auto 0;
          background: #d9be79;
          opacity: .8;
        }
        .ad-b__content {
          position: relative;
          z-index: 2;
          display: flex;
          align-items: center;
          justify-content: center;
          width: 100%;
          height: 100%;
          padding: 72px 20px 54px;
        }
        .ad-b__card {
          width: min(670px, 100%);
          position: relative;
          background: rgba(20, 15, 7, .72);
          border: 1px solid rgba(217, 190, 121, .62);
          box-shadow: 0 18px 80px rgba(0, 0, 0, .32);
          padding: clamp(24px, 4.4vw, 56px) clamp(20px, 5.4vw, 68px) 0;
          animation: adB-rise .8s cubic-bezier(.2,.75,.2,1) both;
        }
        .ad-b__card::before, .ad-b__card::after {
          content: '';
          position: absolute;
          top: -5px;
          height: 9px;
          width: 9px;
          border: 1px solid #d9be79;
          background: #1a1208;
          transform: rotate(45deg);
        }
        .ad-b__card::before { left: 20px; }
        .ad-b__card::after { right: 20px; }
        .ad-b__meta {
          display: flex;
          justify-content: space-between;
          gap: 20px;
          color: #d9be79;
          font: 600 clamp(9px, 1.2vw, 12px)/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
          letter-spacing: .105em;
          text-transform: uppercase;
        }
        .ad-b__meta span { white-space: nowrap; }
        .ad-b__meta b { color: #f5eed8; font-weight: 400; }
        .ad-b__rule {
          height: 1px;
          background: rgba(245, 238, 216, .45);
          margin: clamp(19px, 3vw, 34px) 0 clamp(21px, 3.3vw, 38px);
        }
        .ad-b__headline {
          max-width: 540px;
          margin: 0;
          color: #f5eed8;
          font-size: clamp(36px, 6.3vw, 72px);
          font-weight: 500;
          line-height: .98;
          letter-spacing: -.045em;
        }
        .ad-b__headline em { color: #d9be79; font-style: italic; }
        .ad-b__sub {
          margin: clamp(22px, 3.6vw, 36px) 0 clamp(27px, 4vw, 45px);
          color: rgba(245, 238, 216, .78);
          font: 400 clamp(11px, 1.4vw, 15px)/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
          letter-spacing: .035em;
        }
        .ad-b__cta {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 18px;
          min-height: 58px;
          margin: 0 clamp(-20px, -5.4vw, -68px);
          padding: 17px clamp(20px, 5.4vw, 68px);
          color: #f5eed8;
          background: #2a6b4f;
          text-decoration: none;
          font: 700 clamp(10px, 1.3vw, 13px)/1.2 ui-monospace, SFMono-Regular, Menlo, monospace;
          letter-spacing: .105em;
          text-transform: uppercase;
          transition: background-color .2s ease, color .2s ease;
        }
        .ad-b__cta::after { content: '↗'; color: #d9be79; font-size: 20px; }
        .ad-b__cta:hover, .ad-b__cta:focus-visible { background: #347e5e; color: #fff9e8; outline: none; }
        .ad-b__stamp {
          position: absolute;
          right: clamp(24px, 7vw, 112px);
          bottom: clamp(27px, 6vw, 83px);
          color: #d9be79;
          border: 1px solid rgba(217, 190, 121, .75);
          padding: 8px 10px 7px;
          transform: rotate(-8deg);
          font: 700 9px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
          letter-spacing: .15em;
          text-transform: uppercase;
        }
        @keyframes adB-rise { from { opacity: 0; transform: translateY(18px); } to { opacity: 1; transform: translateY(0); } }
        @media (max-width: 520px) {
          .ad-b__meta { flex-wrap: wrap; row-gap: 10px; }
          .ad-b__headline { font-size: clamp(34px, 11vw, 54px); }
          .ad-b__stamp { display: none; }
          .ad-b__cta { min-height: 64px; }
        }
      `}</style>
      <div className="ad-b__frame" aria-hidden="true" />
      <div className="ad-b__topline">The Brief /</div>
      <div className="ad-b__content">
        <section className="ad-b__card">
          <div className="ad-b__meta" aria-label="Trip brief details">
            <span>Origin: <b>—</b></span>
            <span>Dates: <b>open</b></span>
            <span>Vibe: <b>retro</b></span>
          </div>
          <div className="ad-b__rule" />
          <h1 className="ad-b__headline">4 days. <em>Retro vibes.</em> One stopover.</h1>
          <p className="ad-b__sub">YVR → MEX. Yonder found it in 6 seconds.</p>
          <a className="ad-b__cta" href="https://yonder.city" target="_blank" rel="noreferrer">
            <span>Run your own brief — yonder.city</span>
          </a>
        </section>
      </div>
      <div className="ad-b__stamp" aria-hidden="true">Filed / 06.24</div>
    </main>
  );
}