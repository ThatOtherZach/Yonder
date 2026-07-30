export function IGSquare() {
  const perforations = Array.from({ length: 14 });

  return (
    <main
      style={{
        width: "100vw",
        height: "100vh",
        minWidth: 0,
        minHeight: 0,
        overflow: "hidden",
        position: "relative",
        background: "#f5eed8",
        color: "#1a1208",
        fontFamily: "Georgia, 'Times New Roman', serif",
      }}
    >
      <img
        src="/__mockup/images/yonder-departure-hall.png"
        alt="A traveler leaving an airport in golden evening light"
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          objectFit: "cover",
          objectPosition: "center",
          filter: "sepia(.2) saturate(.82) contrast(1.05)",
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "linear-gradient(90deg, rgba(20,15,7,.8) 0%, rgba(22,16,8,.48) 43%, rgba(22,16,8,.08) 82%), linear-gradient(0deg, rgba(20,15,7,.65) 0%, transparent 42%, rgba(20,15,7,.28) 100%)",
        }}
      />
      {/* Paper grain keeps the image feeling like a printed travel cover. */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          opacity: 0.19,
          mixBlendMode: "screen",
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='grain'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.8' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23grain)' opacity='.32'/%3E%3C/svg%3E\")",
        }}
      />

      <section
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: "7.2% 8.2% 7.2%",
        }}
      >
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <div
              style={{
                color: "#d9be79",
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                fontSize: "clamp(9px, 1.6vw, 12px)",
                letterSpacing: ".22em",
                fontWeight: 700,
              }}
            >
              YONDER CITY
            </div>
            <div
              style={{
                marginTop: 7,
                color: "#f5eed8",
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                fontSize: "clamp(7px, 1.05vw, 9px)",
                letterSpacing: ".13em",
                opacity: .78,
              }}
            >
              AI-POWERED ESCAPE FINDER
            </div>
          </div>
          <div
            style={{
              width: "clamp(45px, 10vw, 67px)",
              height: "clamp(45px, 10vw, 67px)",
              border: "1px solid rgba(245,238,216,.72)",
              borderRadius: "50%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#f5eed8",
              fontFamily: "ui-monospace, monospace",
              fontSize: "clamp(7px, 1.2vw, 9px)",
              letterSpacing: ".13em",
              textAlign: "center",
              lineHeight: 1.25,
              transform: "rotate(13deg)",
            }}
          >
            DEPART<br />ANYTIME
          </div>
        </header>

        <div style={{ marginTop: "auto", marginBottom: "auto", paddingTop: "8%" }}>
          <div
            style={{
              color: "#d9be79",
              fontFamily: "ui-monospace, monospace",
              fontSize: "clamp(8px, 1.35vw, 11px)",
              letterSpacing: ".22em",
              marginBottom: "4%",
            }}
          >
            YOUR NEXT FLIGHT ISN'T WHERE YOU THINK
          </div>
          <h1
            style={{
              margin: 0,
              color: "#f5eed8",
              fontSize: "clamp(64px, 15vw, 108px)",
              lineHeight: ".79",
              letterSpacing: "-.065em",
              fontWeight: 700,
              maxWidth: "75%",
            }}
          >
            Go
            <br />
            Yonder.
          </h1>
          <div style={{ height: 2, width: "clamp(38px, 9vw, 58px)", background: "#2a6b4f", marginTop: "7%" }} />
          <p
            style={{
              margin: "4% 0 0",
              color: "#f5eed8",
              fontFamily: "ui-monospace, monospace",
              fontSize: "clamp(10px, 1.65vw, 13px)",
              letterSpacing: ".13em",
              lineHeight: 1.55,
              textTransform: "uppercase",
            }}
          >
            Type a trip. Get real prices.
          </p>
        </div>

        <footer
          style={{
            borderTop: "1px solid rgba(245,238,216,.55)",
            paddingTop: "4.2%",
            display: "flex",
            alignItems: "flex-end",
            justifyContent: "space-between",
            gap: 12,
          }}
        >
          <div>
            <div
              style={{
                color: "#f5eed8",
                fontFamily: "ui-monospace, monospace",
                fontSize: "clamp(9px, 1.5vw, 12px)",
                letterSpacing: ".18em",
              }}
            >
              BUY THE TICKET,
            </div>
            <div
              style={{
                color: "#d9be79",
                fontFamily: "ui-monospace, monospace",
                fontSize: "clamp(9px, 1.5vw, 12px)",
                letterSpacing: ".18em",
              }}
            >
              TAKE THE RIDE.
            </div>
          </div>
          <div
            style={{
              color: "#f5eed8",
              fontFamily: "ui-monospace, monospace",
              fontSize: "clamp(11px, 1.9vw, 15px)",
              fontWeight: 700,
              letterSpacing: ".08em",
              whiteSpace: "nowrap",
            }}
          >
            yonder.city ↗
          </div>
        </footer>
      </section>

      <div
        style={{
          position: "absolute",
          right: 0,
          top: "14%",
          bottom: "14%",
          width: "3.5%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        {perforations.map((_, index) => (
          <span
            key={index}
            style={{
              width: "clamp(5px, 1vw, 7px)",
              height: "clamp(5px, 1vw, 7px)",
              borderRadius: "50%",
              background: "#f5eed8",
              opacity: .78,
            }}
          />
        ))}
      </div>
    </main>
  );
}