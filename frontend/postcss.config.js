import { fileURLToPath } from "url";
import { dirname, join } from "path";

// Resolve the Tailwind config by absolute path so PostCSS finds it no matter what
// cwd the build/dev server runs from. Tailwind's default config auto-discovery is
// cwd-relative, which silently falls back to an empty config (purging every class)
// when the tool is launched from a parent directory.
const here = dirname(fileURLToPath(import.meta.url));

export default {
  plugins: {
    tailwindcss: { config: join(here, "tailwind.config.js") },
    autoprefixer: {},
  },
};
