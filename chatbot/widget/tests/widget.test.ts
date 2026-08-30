import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "../src/widget";

/** Node 22+'s built-in global `localStorage` shadows jsdom's without `--localstorage-file`,
 * leaving a broken stub on `window.localStorage` — stub in a real in-memory Storage instead
 * of depending on either. */
function createMemoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
    clear: () => store.clear(),
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    get length() {
      return store.size;
    },
  } as Storage;
}

describe("assistant-chat-widget", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", createMemoryStorage());
  });

  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("renders an input and a send button", () => {
    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);

    const shadow = widget.shadowRoot!;
    expect(shadow.querySelector("input")).not.toBeNull();
    expect(shadow.querySelector("button")).not.toBeNull();
  });

  it("sends a message and displays the assistant's response", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        session_id: "s1",
        reply: "Here's what I found: Classic T-Shirt ($19.99)",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const widget = document.createElement("assistant-chat-widget");
    widget.setAttribute("api-base", "http://localhost:8000");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;

    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "show me t-shirts";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => {
      expect(shadow.querySelectorAll(".message.assistant").length).toBe(1);
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/chat",
      expect.objectContaining({ method: "POST" }),
    );
    expect(shadow.querySelector(".message.user")?.textContent).toContain("show me t-shirts");
    expect(shadow.querySelector(".message.assistant")?.textContent).toContain("Classic T-Shirt");
    expect(shadow.querySelector(".message.assistant.confirm")).toBeNull();
  });

  it("visually distinguishes a confirmation-needed reply from a read-only one", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        session_id: "s1",
        reply: "Add 1 x Classic T-Shirt (reply 'yes' to confirm or 'no' to cancel)",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;

    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "add the classic t-shirt to my cart";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => {
      expect(shadow.querySelector(".message.assistant.confirm")).not.toBeNull();
    });
    expect(shadow.querySelector(".badge")?.textContent).toContain("confirmation");
  });

  it("sends X-Assistant-Key when a tenant-key attribute is set", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ session_id: "s1", reply: "Here's what I found: shoes" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const widget = document.createElement("assistant-chat-widget");
    widget.setAttribute("tenant-key", "pk_live_abc123");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;

    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "show me shoes";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });

    const [, requestInit] = fetchMock.mock.calls[0];
    expect(requestInit.headers["X-Assistant-Key"]).toBe("pk_live_abc123");
  });

  it("omits X-Assistant-Key entirely when no tenant-key attribute is set", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ session_id: "s1", reply: "Here's what I found: shoes" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;

    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "show me shoes";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });

    const [, requestInit] = fetchMock.mock.calls[0];
    expect(requestInit.headers["X-Assistant-Key"]).toBeUndefined();
  });

  it("shows a friendly error message when the assistant service is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;

    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "hello";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => {
      expect(shadow.querySelector(".message.assistant")).not.toBeNull();
    });
    expect(shadow.querySelector(".message.assistant")?.textContent).toContain("couldn't reach");
  });
});
