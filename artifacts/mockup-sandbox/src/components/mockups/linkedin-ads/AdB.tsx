/** Variant B — "The Brief"
 *  1200 × 627 px LinkedIn landscape ad.
 *  Desert highway full-bleed with night-sky gradient. Copy styled as a
 *  professional deliverable brief — Yonder's own product language as punchline.
 */
export function AdB() {
  return (
    <div
      style={{
        width: 1200,
        height: 627,
        position: "relative",
        overflow: "hidden",
        fontFamily: "Georgia, 'Times New Roman', serif",
        background: "#0f0d0b",
      }}
    >
      {/* Hero — desert highway, landscape-cropped */}
      <img
        src="/__mockup/images/yonder-desert-highway.png"
        alt="Desert highway winding into a twilight horizon"
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          objectFit: "cover",
          objectPosition: "center 55%",
          filter: "sepia(.12) saturate(.8) contrast(1.1) brightness(.72)",
        }}
      />

      {/* Right-to-left dark vignette — content sits on the left */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "linear-gradient(to right, rgba(12,9,6,.94) 0%, rgba(12,9,6,.82) 38%, rgba(12,9,6,.4) 62%, rgba(12,9,6,.1) 100%), linear-gradient(to top, rgba(8,6,4,.6) 0%, transparent 40%)",
        }}
      />

      {/* Grain */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          opacity: 0.12,
          mixBlendMode: "screen",
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Cfilter id='g'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23g)' opacity='.3'/%3E%3C/svg%3E\")",
        }}
      />

      {/* Content — left column */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "0 68px",
          maxWidth: 620,
        }}
      >
        {/* Document header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            marginBottom: 32,
          }}
        >
          <div
            style={{
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
              fontSize: 10,
              letterSpacing: "0.28em",
              color: "#c8743a",
              fontWeight: 700,
            }}
          >
            YONDER / TRIP BRIEF
          </div>
          <div
            style={{
              flex: 1,
              height: 1,
              background: "rgba(200,116,58,.38)",
              maxWidth: 140,
            }}
          />
        </div>

        {/* Brief block */}
        <div
          style={{
            borderLeft: "2px solid #c8743a",
            paddingLeft: 24,
            marginBottom: 36,
          }}
        >
          <div
            style={{
              fontFamily: "ui-monospace, monospace",
              fontSize: 11,
              letterSpacing: "0.2em",
              color: "rgba(217,190,121,.72)",
              marginBottom: 14,
              textTransform: "uppercase",
            }}
          >
            The brief
          </div>
          <p
            style={{
              margin: 0,
              color: "#f0e8d4",
              fontSize: 22,
              lineHeight: 1.45,
              fontStyle: "italic",
              fontWeight: 400,
            }}
          >
            4 days, open dates, retro vibes.
          </p>
        </div>

        {/* Result block */}
        <div
          style={{
            borderLeft: "2px solid rgba(240,232,212,.18)",
            paddingLeft: 24,
            marginBottom: 40,
          }}
        >
          <div
            style={{
              fontFamily: "ui-monospace, monospace",
              fontSize: 11,
              letterSpacing: "0.2em",
              color: "rgba(217,190,121,.72)",
              marginBottom: 14,
              textTransform: "uppercase",
            }}
          >
            The result
          </div>
          <p
            style={{
              margin: 0,
              color: "#f0e8d4",
              fontSize: 22,
              lineHeight: 1.45,
              fontWeight: 400,
            }}
          >
            YVR → MEX with a stopover that made it worth it.
          </p>
        </div>

        {/* CTA */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 16,
          }}
        >
          <div
            style={{
              background: "#c8743a",
              color: "#1c1410",
              fontFamily: "ui-monospace, monospace",
              fontSize: 12,
              fontWeight: 700,
              letterSpacing: "0.14em",
              padding: "11px 22px",
              textTransform: "uppercase",
            }}
          >
            Run your own brief
          </div>
          <span
            style={{
              fontFamily: "ui-monospace, monospace",
              fontSize: 12,
              letterSpacing: "0.12em",
              color: "rgba(240,232,212,.56)",
            }}
          >
            yonder.city
          </span>
        </div>
      </div>

      {/* Bottom-right wordmark */}
      <div
        style={{
          position: "absolute",
          bottom: 36,
          right: 56,
          fontFamily: "ui-monospace, monospace",
          fontSize: 13,
          fontWeight: 700,
          letterSpacing: "0.08em",
          color: "rgba(240,232,212,.38)",
        }}
      >
        YONDER
      </div>
    </div>
  );
}

export default AdB;
