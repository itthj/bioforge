// Mol* (molstar package) type shim.
//
// We dynamically import a tiny surface of Mol* in StructureCard.tsx, but the
// runtime contract there is narrowed via `as` casts at the call site — we don't
// actually want Mol*'s 4 MB of types in our compile graph. This shim lets `tsc`
// resolve the module specifiers without installing or typing the upstream package.
//
// If Mol* is not installed at runtime, StructureCard's dynamic import throws
// and the component renders an install-hint fallback. No build-time dependency.

declare module "molstar/lib/mol-plugin-ui";
declare module "molstar/lib/mol-plugin-ui/spec";
declare module "molstar/lib/mol-plugin-ui/react18";
