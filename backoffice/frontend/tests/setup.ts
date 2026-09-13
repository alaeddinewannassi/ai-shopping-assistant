import "@testing-library/jest-dom/vitest";

// Node 25+ ships its own global Storage/localStorage implementation that, under this
// project's jsdom + vitest versions, shadows jsdom's window.localStorage with one whose
// methods are all `undefined` unless the process is launched with `--localstorage-file`
// (see the startup warning). Any test that touches localStorage — including src/lib/auth.tsx,
// which reads it directly in a useState initializer — throws immediately in that
// environment. Polyfills a minimal, working in-memory Storage only when the real one is
// non-functional, so tests behave the same regardless of which Node happens to be installed.
if (typeof window.localStorage.clear !== "function") {
  const store = new Map<string, string>();
  const polyfill: Storage = {
    getItem: (key) => (store.has(key) ? store.get(key)! : null),
    setItem: (key, value) => void store.set(key, String(value)),
    removeItem: (key) => void store.delete(key),
    clear: () => store.clear(),
    key: (index) => Array.from(store.keys())[index] ?? null,
    get length() {
      return store.size;
    },
  };
  Object.defineProperty(window, "localStorage", { value: polyfill, configurable: true });
}
