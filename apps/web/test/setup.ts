import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library only auto-registers cleanup when Vitest's globals are on;
// this project keeps them off, so unmount explicitly or renders leak between
// tests in the same file.
afterEach(cleanup);
if (!("EventSource" in globalThis)) {
  class InertEventSource {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSED = 2;
    readonly readyState = 0;
    constructor(_url: string | URL) {
      void _url;
    }
    addEventListener(): void {}
    removeEventListener(): void {}
    close(): void {}
  }
  Object.defineProperty(globalThis, "EventSource", {
    value: InertEventSource,
    writable: true,
    configurable: true,
  });
}
