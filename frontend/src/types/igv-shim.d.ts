// igv.js (igv package) type shim.
//
// IgvGuideViewer.tsx dynamically imports igv.js on the first "Load" click. igv.js
// ships ~3 MB and we only touch a tiny slice of its surface (createBrowser /
// removeBrowser), narrowed via `as` casts at the call site. This shim lets `tsc`
// resolve the module specifier without installing or typing the upstream package.
//
// If igv is not installed at runtime, the dynamic import throws and the component
// renders an install-hint fallback. No build-time dependency (same posture as the
// Mol* shim — see molstar-shim.d.ts).

declare module "igv";
