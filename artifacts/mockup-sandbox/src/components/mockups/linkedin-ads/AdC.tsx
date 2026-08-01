export function AdC() {
  return (
    <main className="ad-c" aria-label="Yonder: The Gap">
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Playfair+Display:ital,wght@0,500;0,600;1,500;1,600&display=swap');

        .ad-c, .ad-c * { box-sizing: border-box; }
        .ad-c {
          --cream: #f5eed8;
          --ink: #0e0b06;
          --green: #2a6b4f;
          --gold: #d9be79;
          position: relative;
          width: 100vw;
          height: 100vh;
          min-height: 100dvh;
          overflow: hidden;
          isolation: isolate;
          color: var(--cream);
          background:
            radial-gradient(ellipse at 50% 42%, rgba(42,107,79,.12) 0%, transparent 47%),
            linear-gradient(124deg, #0e0b06 0%, #171108 58%, #1a1208 100%);
        }

        .ad-c__texture {
          position: absolute;
          z-index: -2;
          inset: 0;
          opacity: .12;
          background: url('/__mockup/images/yonder-departure-hall.png') center / cover no-repeat;
          filter: saturate(.3) contrast(1.25);
          mix-blend-mode: screen;
        }

        .ad-c__grain {
          position: absolute;
          z-index: 4;
          pointer-events: none;
          inset: -50%;
          width: 200%;
          height: 200%;
          opacity: .22;
          mix-blend-mode: screen;
          background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 180 180' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.82' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.8'/%3E%3C/svg%3E");
          animation: ad-c-drift .24s steps(2) infinite;
        }

        .ad-c__rule {
          position: absolute;
          z-index: 1;
          left: 0;
          width: 100%;
          height: 1px;
          background: var(--cream);
          opacity: .25;
          transform-origin: left;
          animation: ad-c-reveal 1.1s ease-out both;
        }
        .ad-c__rule--top { top: 35%; }
        .ad-c__rule--bottom { top: 85%; animation-delay: .18s; }

        .ad-c__center {
          position: relative;
          z-index: 2;
          display: flex;
          width: 100%;
          height: 100%;
          align-items: center;
          justify-content: center;
          padding: 6vh 7vw 10vh;
          text-align: left;
        }

        .ad-c__copy {
          width: min(980px, 100%);
          margin-top: -3vh;
          animation: ad-c-rise 1s cubic-bezier(.22,.8,.22,1) both;
        }

        .ad-c__headline {
          margin: 0;
          color: var(--cream);
          font-family: 'Playfair Display', Georgia, serif;
          font-size: clamp(3.6rem, 10.4vw, 10.2rem);
          font-weight: 500;
          letter-spacing: -.065em;
          line-height: .88;
        }

        .ad-c__headline span { display: block; }
        .ad-c__headline span:nth-child(2) {
          margin-left: clamp(1.25rem, 7.3vw, 7rem);
          font-style: italic;
        }
        .ad-c__headline span:nth-child(3) {
          margin-left: clamp(.3rem, 2.8vw, 2.8rem);
          color: var(--gold);
          font-style: italic;
        }

        .ad-c__mark {
          display: block;
          margin: clamp(3rem, 8vh, 7.5rem) auto 0;
          color: var(--green);
          font-family: 'DM Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: clamp(.74rem, 1.25vw, 1rem);
          font-weight: 500;
          letter-spacing: .2em;
          text-align: center;
        }

        .ad-c__footer {
          position: absolute;
          z-index: 3;
          bottom: 3.8vh;
          left: 0;
          width: 100%;
          color: rgba(245,238,216,.6);
          font-family: 'DM Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: clamp(.55rem, .82vw, .72rem);
          letter-spacing: .18em;
          line-height: 1;
          text-align: center;
          text-transform: uppercase;
          animation: ad-c-fade 1.2s ease-out .5s both;
        }

        @keyframes ad-c-rise {
          from { opacity: 0; transform: translateY(18px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes ad-c-fade {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        @keyframes ad-c-reveal {
          from { transform: scaleX(0); opacity: 0; }
          to { transform: scaleX(1); opacity: .25; }
        }
        @keyframes ad-c-drift {
          0% { transform: translate3d(0,0,0); }
          25% { transform: translate3d(-1.5%, 1%, 0); }
          50% { transform: translate3d(1%, -1.5%, 0); }
          75% { transform: translate3d(1.5%, 1%, 0); }
          100% { transform: translate3d(0,0,0); }
        }
        @media (max-width: 560px) {
          .ad-c__center { padding: 8vh 8vw 12vh; }
          .ad-c__copy { margin-top: -2vh; }
          .ad-c__headline { font-size: clamp(3.25rem, 15vw, 5.8rem); line-height: .91; }
          .ad-c__headline span:nth-child(2) { margin-left: 1rem; }
          .ad-c__headline span:nth-child(3) { margin-left: .15rem; }
          .ad-c__mark { margin-top: 4.2rem; letter-spacing: .14em; }
          .ad-c__footer { bottom: 4.5vh; letter-spacing: .11em; }
        }
        @media (prefers-reduced-motion: reduce) {
          .ad-c__grain { animation: none; }
          .ad-c__copy, .ad-c__footer, .ad-c__rule { animation: none; }
        }
      `}</style>
      <div className="ad-c__texture" aria-hidden="true" />
      <div className="ad-c__grain" aria-hidden="true" />
      <div className="ad-c__rule ad-c__rule--top" aria-hidden="true" />
      <div className="ad-c__rule ad-c__rule--bottom" aria-hidden="true" />
      <div className="ad-c__center">
        <div className="ad-c__copy">
          <h1 className="ad-c__headline">
            <span>That 3-day gap</span>
            <span>in your calendar</span>
            <span>has been waiting.</span>
          </h1>
          <span className="ad-c__mark">yonder.city</span>
        </div>
      </div>
      <footer className="ad-c__footer">AI-POWERED ESCAPE FINDER</footer>
    </main>
  );
}