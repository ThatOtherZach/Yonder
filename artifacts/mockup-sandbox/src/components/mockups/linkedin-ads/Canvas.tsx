/**
 * Canvas — LinkedIn Ads "The Brief" series
 * Shows all three 1200×627 variants scaled side-by-side for comparison.
 * Each ad is transformed to ~390px wide so all three fit on a single view.
 */
import { AdA } from "./AdA";
import { AdB } from "./AdB";
import { AdC } from "./AdC";

const AD_W = 1200;
const AD_H = 627;
const PREVIEW_W = 390;
const SCALE = PREVIEW_W / AD_W;
const PREVIEW_H = Math.round(AD_H * SCALE);

interface AdPreviewProps {
  label: string;
  sublabel: string;
  children: React.ReactNode;
}

function AdPreview({ label, sublabel, children }: AdPreviewProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      {/* Label bar */}
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 10,
          marginBottom: 12,
        }}
      >
        <span
          style={{
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.22em",
            color: "#c8743a",
            textTransform: "uppercase",
          }}
        >
          {label}
        </span>
        <span
          style={{
            fontFamily: "ui-monospace, monospace",
            fontSize: 10,
            letterSpacing: "0.1em",
            color: "rgba(240,232,212,.44)",
            textTransform: "uppercase",
          }}
        >
          — {sublabel}
        </span>
      </div>

      {/* Scaled ad frame */}
      <div
        style={{
          width: PREVIEW_W,
          height: PREVIEW_H,
          overflow: "hidden",
          position: "relative",
          borderRadius: 3,
          boxShadow: "0 4px 32px rgba(0,0,0,.55), 0 1px 4px rgba(0,0,0,.4)",
          outline: "1px solid rgba(200,116,58,.18)",
        }}
      >
        <div
          style={{
            width: AD_W,
            height: AD_H,
            transform: `scale(${SCALE})`,
            transformOrigin: "top left",
          }}
        >
          {children}
        </div>
      </div>

      {/* Dimensions badge */}
      <div
        style={{
          marginTop: 10,
          fontFamily: "ui-monospace, monospace",
          fontSize: 9,
          letterSpacing: "0.14em",
          color: "rgba(240,232,212,.28)",
          textAlign: "center",
        }}
      >
        1200 × 627 px · LinkedIn Landscape
      </div>
    </div>
  );
}

export function Canvas() {
  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#111009",
        padding: "52px 48px 64px",
        fontFamily: "Georgia, 'Times New Roman', serif",
      }}
    >
      {/* Header */}
      <header style={{ marginBottom: 48 }}>
        <div
          style={{
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            fontSize: 10,
            letterSpacing: "0.3em",
            color: "#c8743a",
            fontWeight: 700,
            marginBottom: 14,
          }}
        >
          YONDER / LINKEDIN ADS
        </div>
        <h1
          style={{
            margin: 0,
            color: "#f0e8d4",
            fontSize: 32,
            fontWeight: 700,
            letterSpacing: "-0.02em",
            lineHeight: 1.15,
          }}
        >
          The Brief Series
        </h1>
        <p
          style={{
            margin: "10px 0 0",
            color: "rgba(240,232,212,.48)",
            fontFamily:
              "'Helvetica Neue', Arial, sans-serif",
            fontSize: 14,
            lineHeight: 1.5,
            fontWeight: 400,
            maxWidth: 520,
          }}
        >
          Three 1200×627 px LinkedIn ad variants targeting frequent business
          travelers. Aspirational and self-aware — professional language
          repurposed for travel discovery.
        </p>
        <div
          style={{
            width: 40,
            height: 2,
            background: "#c8743a",
            marginTop: 24,
            opacity: 0.7,
          }}
        />
      </header>

      {/* Ad previews — side-by-side */}
      <div
        style={{
          display: "flex",
          gap: 32,
          alignItems: "flex-start",
          overflowX: "auto",
          paddingBottom: 8,
        }}
      >
        <AdPreview label="Variant A" sublabel="Already in the Air">
          <AdA />
        </AdPreview>

        <AdPreview label="Variant B" sublabel="The Brief">
          <AdB />
        </AdPreview>

        <AdPreview label="Variant C" sublabel="The Gap">
          <AdC />
        </AdPreview>
      </div>

      {/* Copy reference sheet */}
      <div
        style={{
          marginTop: 56,
          borderTop: "1px solid rgba(240,232,212,.1)",
          paddingTop: 40,
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 32,
        }}
      >
        {[
          {
            variant: "A",
            name: "Already in the Air",
            headline:
              "You fly 40+ times a year. How many of those had a detour worth taking?",
            sub: "Yonder finds the ones that do.",
            cta: "yonder.city ↗",
          },
          {
            variant: "B",
            name: "The Brief",
            headline:
              "The brief: 4 days, open dates, retro vibes. The result: YVR → MEX with a stopover that made it worth it.",
            sub: "",
            cta: "Run your own brief at yonder.city",
          },
          {
            variant: "C",
            name: "The Gap",
            headline:
              "That 3-day gap in your calendar has been waiting.",
            sub: "",
            cta: "Yonder.city",
          },
        ].map(({ variant, name, headline, sub, cta }) => (
          <div key={variant}>
            <div
              style={{
                fontFamily: "ui-monospace, monospace",
                fontSize: 9,
                letterSpacing: "0.22em",
                color: "#c8743a",
                fontWeight: 700,
                marginBottom: 10,
              }}
            >
              VARIANT {variant} — {name.toUpperCase()}
            </div>
            <p
              style={{
                margin: 0,
                color: "#f0e8d4",
                fontSize: 13,
                lineHeight: 1.6,
                fontStyle: "italic",
              }}
            >
              "{headline}"
            </p>
            {sub && (
              <p
                style={{
                  margin: "8px 0 0",
                  color: "rgba(240,232,212,.6)",
                  fontFamily: "'Helvetica Neue', Arial, sans-serif",
                  fontSize: 12,
                  lineHeight: 1.5,
                }}
              >
                {sub}
              </p>
            )}
            <p
              style={{
                margin: "12px 0 0",
                fontFamily: "ui-monospace, monospace",
                fontSize: 11,
                letterSpacing: "0.1em",
                color: "#d9be79",
              }}
            >
              {cta}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

export default Canvas;
