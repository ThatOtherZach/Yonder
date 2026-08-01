/** Variant C — "The Gap"
 *  1200 × 627 px LinkedIn landscape ad.
 *  Dark, minimal, typography-led. One headline, one wordmark. Intentional silence.
 */
export function AdC() {
  return (
    <div
      style={{
        width: 1200,
        height: 627,
        position: "relative",
        overflow: "hidden",
        fontFamily: "Georgia, 'Times New Roman', serif",
        background: "#111009",
      }}
    >
      {/* Departure hall — extreme darkness, used as texture not image */}
      <img
        src="/__mockup/images/yonder-departure-hall.png"
        alt=""
        aria-hidden="true"
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          objectFit: "cover",
          objectPosition: "center",
          filter: "sepia(.2) saturate(.5) contrast(.9) brightness(.22)",
          opacity: 0.55,
        }}
      />

      {/* Near-black full overlay — image becomes whisper-level texture */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "rgba(10,8,6,.82)",
        }}
      />

      {/* Subtle warm vignette — center slightly lighter, edges dark */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(ellipse 70% 55% at 50% 50%, rgba(200,116,58,.05) 0%, rgba(0,0,0,.0) 60%, rgba(0,0,0,.28) 100%)",
        }}
      />

      {/* Grain layer */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          opacity: 0.16,
          mixBlendMode: "screen",
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Cfilter id='g'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23g)' opacity='.3'/%3E%3C/svg%3E\")",
        }}
      />

      {/* Centered content block */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 0,
          padding: "0 96px",
          textAlign: "center",
        }}
      >
        {/* Amber rule above */}
        <div
          style={{
            width: 36,
            height: 1,
            background: "#c8743a",
            marginBottom: 40,
            opacity: 0.7,
          }}
        />

        {/* The single line */}
        <h1
          style={{
            margin: 0,
            color: "#f0e8d4",
            fontSize: 62,
            fontWeight: 700,
            lineHeight: 1.1,
            letterSpacing: "-0.02em",
            maxWidth: 860,
          }}
        >
          That 3-day gap in your calendar
          <br />
          <span
            style={{
              color: "#d9be79",
              fontStyle: "italic",
              fontWeight: 400,
            }}
          >
            has been waiting.
          </span>
        </h1>

        {/* Wordmark — minimal, no explanation */}
        <div
          style={{
            marginTop: 52,
            fontFamily:
              "ui-monospace, SFMono-Regular, Menlo, monospace",
            fontSize: 16,
            fontWeight: 700,
            letterSpacing: "0.24em",
            color: "rgba(240,232,212,.55)",
            textTransform: "uppercase",
          }}
        >
          Yonder.city
        </div>

        {/* Amber rule below */}
        <div
          style={{
            width: 36,
            height: 1,
            background: "#c8743a",
            marginTop: 36,
            opacity: 0.7,
          }}
        />
      </div>
    </div>
  );
}

export default AdC;
