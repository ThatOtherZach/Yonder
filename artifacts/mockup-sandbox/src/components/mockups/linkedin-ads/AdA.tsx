export function AdA() {
  return (
    <main className="linkedin-ad-a" aria-label="Yonder City LinkedIn advertisement">
      <style>{`
        .linkedin-ad-a {
          --cream: #f5eed8;
          --ink: #1a1208;
          --forest: #2a6b4f;
          --gold: #d9be79;
          position: relative;
          width: 100vw;
          height: 100vh;
          min-height: 100dvh;
          overflow: hidden;
          isolation: isolate;
          color: var(--cream);
          background: var(--ink);
          font-family: Playfair Display, Georgia, serif;
        }

        .linkedin-ad-a *, .linkedin-ad-a *::before, .linkedin-ad-a *::after {
          box-sizing: border-box;
        }

        .linkedin-ad-a__photo {
          position: absolute;
          inset: 0;
          z-index: -3;
          background:
            linear-gradient(90deg, rgba(26,18,8,.98) 0%, rgba(26,18,8,.91) 23%,
              rgba(26,18,8,.68) 47%, rgba(26,18,8,.17) 75%, rgba(26,18,8,.08) 100%),
            linear-gradient(0deg, rgba(26,18,8,.52) 0%, transparent 30%),
            url('/__mockup/images/yonder-departure-hall.png') center center / cover no-repeat;
          transform: scale(1.015);
        }

        .linkedin-ad-a__grain {
          position: absolute;
          inset: -25%;
          z-index: 4;
          pointer-events: none;
          opacity: .18;
          mix-blend-mode: screen;
          background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 180 180' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.82' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.85'/%3E%3C/svg%3E");
          animation: linkedin-grain .22s steps(2) infinite;
        }

        @keyframes linkedin-grain {
          0% { transform: translate3d(0,0,0); }
          25% { transform: translate3d(2%, -1%, 0); }
          50% { transform: translate3d(-1%, 2%, 0); }
          75% { transform: translate3d(1%, 1%, 0); }
          100% { transform: translate3d(-2%, -1%, 0); }
        }

        .linkedin-ad-a__content {
          position: relative;
          height: 100%;
          display: flex;
          flex-direction: column;
          justify-content: space-between;
          padding: clamp(28px, 6.5vh, 72px) clamp(24px, 7vw, 112px) clamp(22px, 5vh, 56px);
        }

        .linkedin-ad-a__eyebrow {
          color: var(--gold);
          font-family: DM Mono, ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: clamp(9px, 1.05vw, 14px);
          font-weight: 600;
          letter-spacing: .24em;
          line-height: 1;
          text-transform: uppercase;
        }

        .linkedin-ad-a__copy {
          width: min(58vw, 720px);
          margin-top: auto;
          margin-bottom: auto;
          padding-top: 3vh;
        }

        .linkedin-ad-a__title {
          max-width: 720px;
          margin: 0;
          color: var(--cream);
          font-size: clamp(48px, 8.15vw, 124px);
          font-weight: 500;
          letter-spacing: -.055em;
          line-height: .93;
          text-wrap: balance;
        }

        .linkedin-ad-a__rule {
          width: clamp(105px, 16vw, 220px);
          height: 2px;
          margin: clamp(24px, 4.2vh, 48px) 0 clamp(18px, 3.5vh, 38px);
          background: var(--forest);
        }

        .linkedin-ad-a__subhead {
          max-width: 535px;
          margin: 0;
          color: rgba(245, 238, 216, .78);
          font-size: clamp(22px, 3.2vw, 52px);
          font-weight: 400;
          letter-spacing: -.035em;
          line-height: 1.08;
          text-wrap: balance;
        }

        .linkedin-ad-a__footer {
          position: relative;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 24px;
          min-height: 46px;
          padding-top: 18px;
          border-top: 1px solid rgba(245, 238, 216, .64);
          color: rgba(245, 238, 216, .88);
          font-family: DM Mono, ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: clamp(8px, 1vw, 13px);
          font-weight: 500;
          letter-spacing: .11em;
          line-height: 1.3;
          text-transform: uppercase;
        }

        .linkedin-ad-a__url {
          color: var(--cream);
          font-weight: 700;
          letter-spacing: .04em;
          text-decoration: none;
          text-transform: none;
          white-space: nowrap;
        }

        .linkedin-ad-a__url:hover { color: var(--gold); }

        .linkedin-ad-a__stamp {
          position: absolute;
          right: clamp(24px, 7vw, 112px);
          top: 27%;
          width: clamp(82px, 10vw, 138px);
          aspect-ratio: 1;
          display: grid;
          place-items: center;
          border: 1px solid rgba(217, 190, 121, .8);
          border-radius: 50%;
          color: var(--gold);
          font-family: DM Mono, ui-monospace, monospace;
          font-size: clamp(7px, .82vw, 11px);
          font-weight: 600;
          letter-spacing: .08em;
          line-height: 1.15;
          text-align: center;
          text-transform: uppercase;
          transform: rotate(12deg);
        }

        .linkedin-ad-a__stamp::before {
          position: absolute;
          inset: 6px;
          border: 1px dashed rgba(217, 190, 121, .72);
          border-radius: 50%;
          content: "";
        }

        .linkedin-ad-a__stamp span { position: relative; }

        .linkedin-ad-a__perforations {
          position: absolute;
          top: 0;
          right: 0;
          bottom: 0;
          width: 12px;
          opacity: .56;
          background: radial-gradient(circle at 100% 50%, transparent 0 3px, var(--cream) 3.4px 4px, transparent 4.4px) repeat-y;
          background-size: 12px 18px;
        }

        @media (max-width: 650px) {
          .linkedin-ad-a__photo {
            background:
              linear-gradient(90deg, rgba(26,18,8,.97) 0%, rgba(26,18,8,.82) 48%, rgba(26,18,8,.32) 100%),
              linear-gradient(0deg, rgba(26,18,8,.64) 0%, transparent 45%),
              url('/__mockup/images/yonder-departure-hall.png') 62% center / cover no-repeat;
          }
          .linkedin-ad-a__content { padding: 32px 24px 24px; }
          .linkedin-ad-a__copy { width: 90vw; padding-top: 0; }
          .linkedin-ad-a__title { font-size: clamp(48px, 15vw, 78px); }
          .linkedin-ad-a__subhead { max-width: 330px; font-size: clamp(24px, 7vw, 36px); }
          .linkedin-ad-a__stamp { top: 14%; right: 9%; width: 78px; }
          .linkedin-ad-a__footer { align-items: flex-end; }
          .linkedin-ad-a__footer > span:first-child { max-width: 190px; }
        }
      `}</style>
      <div className="linkedin-ad-a__photo" aria-hidden="true" />
      <div className="linkedin-ad-a__grain" aria-hidden="true" />
      <div className="linkedin-ad-a__stamp" aria-hidden="true">
        <span>DETOUR<br />WORTH<br />TAKING<br />✦ 01 ✦</span>
      </div>
      <div className="linkedin-ad-a__content">
        <div className="linkedin-ad-a__eyebrow">Yonder City</div>
        <section className="linkedin-ad-a__copy">
          <h1 className="linkedin-ad-a__title">You fly 40+ times a year.</h1>
          <div className="linkedin-ad-a__rule" />
          <p className="linkedin-ad-a__subhead">How many of those had a detour worth taking?</p>
        </section>
        <footer className="linkedin-ad-a__footer">
          <span>Buy the ticket, take the ride.</span>
          <a className="linkedin-ad-a__url" href="https://yonder.city" aria-label="Visit yonder.city">yonder.city ↗</a>
        </footer>
      </div>
      <div className="linkedin-ad-a__perforations" aria-hidden="true" />
    </main>
  );
}