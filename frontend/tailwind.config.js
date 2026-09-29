/** @type {import("tailwindcss").Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Deep neutral base — imagery supplies the colour, the interface stays out of
        // the way. Not a purple-gradient AI template.
        void:    "#05070a",
        abyss:   "#080b11",
        panel:   "#0d1118",
        raised:  "#121722",
        line:    "#1c2430",
        "line-bright": "#2a3542",
        muted:   "#6b7a8d",
        dim:     "#8b9aad",
        bright:  "#dce5f0",
        // A single restrained accent: instrument cyan.
        signal:  { DEFAULT: "#38d0d8", dim: "#1d8d96", glow: "#7fe8ee" },
        warn:    "#e0a458",
        danger:  "#e06c75",
        ok:      "#6cc394",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.04em" }],
      },
      letterSpacing: { tightest: "-0.04em" },
      animation: {
        "fade-up": "fadeUp .7s cubic-bezier(.16,1,.3,1) both",
        "fade-in": "fadeIn .5s ease both",
        scan: "scan 3.5s linear infinite",
        "pulse-ring": "pulseRing 2.4s cubic-bezier(.4,0,.6,1) infinite",
      },
      keyframes: {
        fadeUp: { "0%": { opacity: "0", transform: "translateY(14px)" },
                  "100%": { opacity: "1", transform: "none" } },
        fadeIn: { "0%": { opacity: "0" }, "100%": { opacity: "1" } },
        scan: { "0%": { transform: "translateY(-100%)" },
                "100%": { transform: "translateY(100%)" } },
        pulseRing: { "0%,100%": { opacity: "0.35" }, "50%": { opacity: "0.9" } },
      },
    },
  },
  plugins: [],
};
