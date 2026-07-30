export function Front() {
  return (
    <div
      style={{
        width: "100vw",
        height: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#1a1208",
      }}
    >
      <div
        style={{
          width: 700,
          height: 400,
          position: "relative",
          background: "linear-gradient(135deg, #f5eed8 0%, #ede4c8 40%, #e8dcc0 100%)",
          overflow: "hidden",
          fontFamily: "'Georgia', 'Times New Roman', serif",
          boxShadow: "0 8px 40px rgba(0,0,0,0.45)",
        }}
      >
        {/* Paper grain texture overlay */}
        <div style={{
          position: "absolute", inset: 0,
          backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='300' height='300'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='300' height='300' filter='url(%23n)' opacity='0.07'/%3E%3C/svg%3E")`,
          opacity: 0.5,
          pointerEvents: "none",
        }} />

        {/* Left accent stripe */}
        <div style={{
          position: "absolute", left: 0, top: 0, bottom: 0, width: 6,
          background: "linear-gradient(180deg, #2a6b4f 0%, #1d5a40 100%)",
        }} />

        {/* Perforation dots on right */}
        <div style={{
          position: "absolute", right: 0, top: 0, bottom: 0, width: 28,
          display: "flex", flexDirection: "column", alignItems: "center",
          justifyContent: "space-evenly", paddingTop: 14, paddingBottom: 14,
        }}>
          {Array.from({ length: 16 }).map((_, i) => (
            <div key={i} style={{
              width: 7, height: 7, borderRadius: "50%",
              background: "#1a1208", opacity: 0.12,
            }} />
          ))}
        </div>

        {/* Vertical "BOARDING PASS" side label */}
        <div style={{
          position: "absolute", right: 32, top: "50%",
          transform: "translateY(-50%) rotate(90deg)",
          fontSize: 9, letterSpacing: "0.25em", fontFamily: "ui-monospace, monospace",
          color: "#8a7a5a", textTransform: "uppercase", whiteSpace: "nowrap",
          opacity: 0.5,
        }}>
          OPEN ITINERARY · DEPART ANYTIME
        </div>

        {/* Main content area */}
        <div style={{
          position: "absolute", left: 28, right: 60, top: 0, bottom: 0,
          display: "flex", flexDirection: "column", justifyContent: "center",
          paddingLeft: 14, paddingRight: 20,
        }}>
          {/* Top eyebrow */}
          <div style={{
            fontSize: 9.5, letterSpacing: "0.22em", textTransform: "uppercase",
            color: "#2a6b4f", fontFamily: "ui-monospace, monospace",
            marginBottom: 16, fontWeight: 600,
          }}>
            ✈ YONDER CITY
          </div>

          {/* Hero headline */}
          <div style={{
            fontSize: 72, fontWeight: 700, lineHeight: 0.88,
            color: "#1a1208", letterSpacing: "-0.03em",
            marginBottom: 18,
          }}>
            Go<br />Yonder
          </div>

          {/* Divider rule */}
          <div style={{
            width: 48, height: 2, background: "#2a6b4f",
            marginBottom: 16, opacity: 0.7,
          }} />

          {/* Tagline */}
          <div style={{
            fontSize: 13, letterSpacing: "0.12em", textTransform: "uppercase",
            color: "#5a4a2a", lineHeight: 1.5, fontFamily: "ui-monospace, monospace",
          }}>
            Buy the Ticket,<br />Take the Ride.
          </div>
        </div>

        {/* Bottom URL */}
        <div style={{
          position: "absolute", bottom: 20, left: 28, right: 60,
          paddingLeft: 14,
          display: "flex", alignItems: "center", justifyContent: "space-between",
        }}>
          <div style={{
            fontSize: 13.5, letterSpacing: "0.06em",
            color: "#2a6b4f", fontWeight: 700,
            fontFamily: "ui-monospace, monospace",
          }}>
            yonder.city
          </div>
          <div style={{
            width: 28, height: 28, borderRadius: "50%",
            border: "1.5px solid rgba(42,107,79,0.3)",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 13, color: "#2a6b4f", opacity: 0.7,
          }}>
            ✈
          </div>
        </div>

        {/* Top-right stamp */}
        <div style={{
          position: "absolute", top: 20, right: 44,
          width: 44, height: 44, borderRadius: "50%",
          border: "2px solid rgba(42,107,79,0.2)",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 20, color: "rgba(42,107,79,0.35)",
          transform: "rotate(15deg)",
        }}>
          🌍
        </div>
      </div>
    </div>
  );
}
