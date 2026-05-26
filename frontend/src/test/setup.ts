// Vitest setup file. Registered via vite.config.ts's `test.setupFiles`.
// - Wires up @testing-library/jest-dom matchers (`.toBeInTheDocument()`, etc.)
// - Runs cleanup() after every test so DOM state doesn't bleed across cases.

import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
