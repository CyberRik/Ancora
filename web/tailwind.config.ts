import type { Config } from "tailwindcss";

// Token surface via CSS variables (defined in app/globals.css).
const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        muted: "hsl(var(--muted))",
        "muted-foreground": "hsl(var(--muted-foreground))",
        card: "hsl(var(--card))",
        elevated: "hsl(var(--elevated))",
        border: "hsl(var(--border))",
        "border-strong": "hsl(var(--border-strong))",
        ring: "hsl(var(--ring))",
        accent: "hsl(var(--accent))",
        "accent-foreground": "hsl(var(--accent-foreground))",
        success: "hsl(var(--success))",
        warning: "hsl(var(--warning))",
        danger: "hsl(var(--danger))",
        flow: "hsl(var(--flow))",
      },
      borderRadius: {
        xl: "0.875rem",
        lg: "0.625rem",
        md: "0.4375rem",
        sm: "0.3125rem",
      },
      fontFamily: {
        // Inter is the gold standard for clean, premium interfaces, while
        // JetBrains Mono ensures technical data like run IDs look sharp.
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      fontSize: {
        // Tightened leading on display sizes; the defaults are set for prose.
        "2xl": ["1.5rem", { lineHeight: "1.9rem", letterSpacing: "-0.018em" }],
        "3xl": ["1.875rem", { lineHeight: "2.25rem", letterSpacing: "-0.022em" }],
        "4xl": ["2.25rem", { lineHeight: "2.5rem", letterSpacing: "-0.026em" }],
      },
      boxShadow: {
        // Dark UI: elevation reads as a lifted border plus a soft drop, never
        // as a grey haze.
        card: "0 1px 2px 0 hsl(0 0% 0% / 0.28)",
        raised:
          "0 1px 0 0 hsl(var(--foreground) / 0.04) inset, 0 2px 8px -2px hsl(0 0% 0% / 0.4)",
        popover: "0 12px 32px -8px hsl(0 0% 0% / 0.6)",
      },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "none" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.28s cubic-bezier(0.22, 1, 0.36, 1) both",
      },
    },
  },
  plugins: [],
};

export default config;
