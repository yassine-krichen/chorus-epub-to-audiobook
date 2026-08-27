// Test-only stand-in for the "server-only" package. The real package throws
// when imported outside a "react-server" bundler condition, which Vitest
// (plain Node) never sets — see vitest.config.ts's resolve.alias. Aliasing
// to this no-op keeps server-only source files importable under test without
// weakening the real runtime guard, since this file is never bundled by Next.js.
export {};
