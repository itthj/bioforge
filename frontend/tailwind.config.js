import { fileURLToPath } from "url";
import { dirname, join } from "path";

// Anchor content globs to this file's directory so Tailwind scans the right files
// no matter what cwd the build/dev server is launched from (cwd-relative globs
// silently purge every class when invoked from a parent dir).
const here = dirname(fileURLToPath(import.meta.url));

/** @type {import('tailwindcss').Config} */
export default {
  content: [join(here, "index.html"), join(here, "src/**/*.{ts,tsx}")],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // All map to CSS variables defined in src/index.css.
        bg: "var(--bg)",
        surface: {
          DEFAULT: "var(--surface)",
          2: "var(--surface-2)",
        },
        border: "var(--border)",
        fg: {
          DEFAULT: "var(--fg)",
          muted: "var(--fg-muted)",
          subtle: "var(--fg-subtle)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          fg: "var(--accent-fg)",
        },
        success: "var(--success)",
        warn: "var(--warn)",
        danger: "var(--danger)",
      },
      fontFamily: {
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
      },
      keyframes: {
        // Functional feedback only — a quiet pulse on the active step's status dot,
        // and a soft entrance for newly streamed steps. No decorative motion.
        "pulse-dot": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.3" },
        },
        "fade-in": {
          from: { opacity: "0", transform: "translateY(2px)" },
          to: { opacity: "1", transform: "none" },
        },
      },
      animation: {
        "pulse-dot": "pulse-dot 1.4s ease-in-out infinite",
        "fade-in": "fade-in 160ms ease-out",
      },
    },
  },
  plugins: [],
};
