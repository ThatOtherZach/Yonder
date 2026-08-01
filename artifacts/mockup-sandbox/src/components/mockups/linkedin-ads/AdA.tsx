/** Variant A — "Already in the Air"
 *  1200 × 627 px LinkedIn landscape ad.
 *  Departure hall full-bleed, dark gradient, large serif headline.
 */
export function AdA() {
  return (
    <div
      style={{
        width: 1200,
        height: 627,
        position: "relative",
        overflow: "hidden",
        fontFamily: "Georgia, 'Times New Roman', serif",
        background: "#1c1814",
      }}
    >
      {/* Hero image */}
      <img
        src="/__mockup/images/yonder-departure-hall.png"
        alt="Business traveler walking through a golden airport departure hall"
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          objectFit: "cover",
          objectPosition: "center 40%",
          filter: "sepia(.15) saturate(.9) contrast(1.05) brightness(.88)",
        }}
      />

      {/* Gradient overlay — strong bottom-up, soft top darkening */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "linear-gradient(to top, rgba(20,14,9,.97) 0%, rgba(20,14,9,.78) 35%, rgba(20,14,9,.22) 65%, rgba(20,14,9,.46) 100%)",
        }}
      />

      {/* Subtle film grain */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          opacity: 0.14,
          mixBlendMode: "screen",
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Cfilter id='g'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23g)' opacity='.3'/%3E%3C/svg%3E\")",
        }}
      />

      {/* Content layout */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "column",
          justifyContent: "flex-end",
          padding: "0 72px 52px",
        }}
      >
        {/* Top-left badge */}
        <div
          style={{
            position: "absolute",
            top: 40,
            left: 72,
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <div
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: "#c8743a",
            }}
          />
          <span
            style={{
              fontFamily:
                "ui-monospace, SFMono-Regular, Menlo, monospace",
              fontSize: 11,
              letterSpacing: "0.22em",
              color: "#d9be79",
              fontWeight: 600,
            }}
          >
            YONDER
          </span>
        </div>

        {/* Amber rule */}
        <div
          style={{
            width: 48,
            height: 2,
            background: "#c8743a",
            marginBottom: 28,
          }}
        />

        {/* Headline */}
        <h1
          style={{
            margin: 0,
            color: "#f0e8d4",
            fontSize: 58,
            lineHeight: 1.08,
            fontWeight: 700,
            letterSpacing: "-0.02em",
            maxWidth: 720,
          }}
        >
          You fly 40+ times a year.
          <br />
          How many of those had a
          <br />
          <span style={{ color: "#d9be79" }}>detour worth taking?</span>
        </h1>

        {/* Sub-copy */}
        <p
          style={{
            margin: "22px 0 0",
            color: "rgba(240,232,212,.72)",
            fontFamily:
              "'Helvetica Neue', Arial, sans-serif",
            fontSize: 20,
            fontWeight: 400,
            letterSpacing: "0.01em",
            lineHeight: 1.4,
          }}
        >
          Yonder finds the ones that do.
        </p>

        {/* Footer row */}
        <div
          style={{
            marginTop: 40,
            display: "flex",
            alignItems: "flex-end",
            justifyContent: "space-between",
          }}
        >
          <div
            style={{
              borderTop: "1px solid rgba(240,232,212,.2)",
              paddingTop: 16,
              width: "100%",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <span
              style={{
                fontFamily: "ui-monospace, monospace",
                fontSize: 11,
                letterSpacing: "0.2em",
                color: "rgba(240,232,212,.42)",
                textTransform: "uppercase",
              }}
            >
              AI-Powered Escape Finder
            </span>
            <span
              style={{
                fontFamily: "ui-monospace, monospace",
                fontSize: 15,
                fontWeight: 700,
                letterSpacing: "0.06em",
                color: "#f0e8d4",
              }}
            >
              yonder.city ↗
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default AdA;
