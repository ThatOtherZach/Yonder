export function Back() {
  const qrUrl =
    "https://api.qrserver.com/v1/create-qr-code/?size=260x260&data=https%3A%2F%2Fyonder.city&color=1a1208&bgcolor=f5eed8&margin=0&qzone=1";

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
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {/* Paper grain */}
        <div style={{
          position: "absolute", inset: 0,
          backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='300' height='300'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='300' height='300' filter='url(%23n)' opacity='0.07'/%3E%3C/svg%3E")`,
          opacity: 0.5, pointerEvents: "none",
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

        {/* Center content */}
        <div style={{
          display: "flex", flexDirection: "column", alignItems: "center",
          gap: 14, zIndex: 1,
        }}>
          {/* Eyebrow */}
          <div style={{
            fontSize: 9, letterSpacing: "0.3em", textTransform: "uppercase",
            color: "#8a7a5a", fontFamily: "ui-monospace, monospace", fontWeight: 600,
          }}>
            SCAN TO ESCAPE
          </div>

          {/* QR code card */}
          <div style={{
            background: "#f5eed8",
            border: "1.5px solid rgba(42,107,79,0.25)",
            borderRadius: 8,
            padding: 10,
            boxShadow: "0 2px 12px rgba(26,18,8,0.1)",
          }}>
            <img
              src={qrUrl}
              alt="QR code for yonder.city"
              width={130}
              height={130}
              style={{ display: "block", imageRendering: "pixelated" }}
            />
          </div>

          {/* URL */}
          <div style={{
            fontSize: 15, letterSpacing: "0.08em",
            color: "#2a6b4f", fontWeight: 700,
            fontFamily: "ui-monospace, monospace",
          }}>
            yonder.city
          </div>

          {/* Sub text */}
          <div style={{
            fontSize: 9, letterSpacing: "0.18em", textTransform: "uppercase",
            color: "#8a7a5a", fontFamily: "ui-monospace, monospace",
            opacity: 0.8,
          }}>
            ✈ &nbsp; Find your next escape
          </div>
        </div>

        {/* Corner stamp marks */}
        <div style={{
          position: "absolute", top: 16, left: 20,
          fontSize: 9, color: "#8a7a5a", fontFamily: "ui-monospace, monospace",
          letterSpacing: "0.15em", opacity: 0.45,
        }}>
          YDR-001
        </div>
        <div style={{
          position: "absolute", bottom: 16, right: 48,
          fontSize: 9, color: "#8a7a5a", fontFamily: "ui-monospace, monospace",
          letterSpacing: "0.1em", opacity: 0.45,
        }}>
          OPEN ITINERARY
        </div>
      </div>
    </div>
  );
}
