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
    // stubGlobal (fetch, prestashop, ...) isn't reset by restoreAllMocks — without this, a
    // window.prestashop stub set by one test would leak into the next.
    vi.unstubAllGlobals();
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
        needs_confirmation: false,
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
        // needs_confirmation is structural (from ChatResponse), not inferred from the reply
        // text — the phrasing itself can vary since agent/llm_client.py's phrase_reply may
        // rewrite it naturally.
        reply: "Sounds good! Shall I add one Classic T-Shirt to your cart?",
        needs_confirmation: true,
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

  it("sends customer_email when PrestaShop reports a logged-in shopper", async () => {
    vi.stubGlobal("prestashop", { customer: { is_logged: true, email: "shopper@example.com" } });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ session_id: "s1", reply: "Here's what I found: shoes", needs_confirmation: false }),
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
    expect(JSON.parse(requestInit.body).customer_email).toBe("shopper@example.com");
  });

  it("omits customer_email for an anonymous/guest shopper", async () => {
    vi.stubGlobal("prestashop", { customer: { is_logged: false, email: null } });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ session_id: "s1", reply: "Here's what I found: shoes", needs_confirmation: false }),
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
    expect(JSON.parse(requestInit.body).customer_email).toBeUndefined();
  });

  it("renders real product links pointing at the store's own product controller URL", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        session_id: "s1",
        reply: "Here's what I found: Classic T-Shirt ($19.99)",
        needs_confirmation: false,
        product_links: [{ id: "prod-tshirt-1", name: "Classic T-Shirt" }],
        show_cart_link: false,
      }),
    }));

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;
    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "show me t-shirts";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => {
      expect(shadow.querySelector(".links a")).not.toBeNull();
    });
    const link = shadow.querySelector<HTMLAnchorElement>(".links a")!;
    expect(link.href).toBe(`${window.location.origin}/index.php?id_product=prod-tshirt-1&controller=product`);
    expect(link.textContent).toContain("Classic T-Shirt");
  });

  it("renders a cart link when the reply is cart-adjacent", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        session_id: "s1",
        reply: "Add 1 x Classic T-Shirt?",
        needs_confirmation: true,
        product_links: [],
        show_cart_link: true,
      }),
    }));

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;
    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "add the classic t-shirt to my cart";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => {
      expect(shadow.querySelector(".links a")).not.toBeNull();
    });
    const link = shadow.querySelector<HTMLAnchorElement>(".links a")!;
    expect(link.href).toBe(`${window.location.origin}/index.php?controller=cart`);
  });

  describe("auto-navigation", () => {
    let originalLocation: Location;
    let setHref: ReturnType<typeof vi.fn>;

    beforeEach(() => {
      // jsdom doesn't implement real navigation — replace window.location with a spy-able
      // stand-in so we can assert what the widget tried to navigate to, without jsdom's
      // "Not implemented: navigation" error.
      originalLocation = window.location;
      setHref = vi.fn();
      Object.defineProperty(window, "location", {
        configurable: true,
        value: { origin: originalLocation.origin, set href(v: string) { setHref(v); } },
      });
    });

    afterEach(() => {
      Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
    });

    it("auto-navigates to a single resolved product's real page", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          session_id: "s1",
          reply: "Here's what I found: Classic T-Shirt ($19.99)",
          needs_confirmation: false,
          product_links: [{ id: "prod-tshirt-1", name: "Classic T-Shirt" }],
          show_cart_link: false,
          auto_navigate_product_id: "prod-tshirt-1",
          auto_navigate_to_cart: false,
        }),
      }));

      const widget = document.createElement("assistant-chat-widget");
      document.body.appendChild(widget);
      const shadow = widget.shadowRoot!;
      const input = shadow.querySelector<HTMLInputElement>("input")!;
      const form = shadow.querySelector<HTMLFormElement>("form")!;
      input.value = "show me the classic t-shirt";
      form.dispatchEvent(new Event("submit", { cancelable: true }));

      await vi.waitFor(() => {
        expect(setHref).toHaveBeenCalled();
      });
      expect(setHref).toHaveBeenCalledWith(
        `${originalLocation.origin}/index.php?id_product=prod-tshirt-1&controller=product`,
      );
    });

    it("does not auto-navigate when already on that product's page", async () => {
      vi.stubGlobal("prestashop", {
        page: { page_name: "product", body_classes: { "product-id-prod-tshirt-1": true } },
      });
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          session_id: "s1",
          reply: "Here's what I found: Classic T-Shirt ($19.99)",
          needs_confirmation: false,
          product_links: [{ id: "prod-tshirt-1", name: "Classic T-Shirt" }],
          show_cart_link: false,
          auto_navigate_product_id: "prod-tshirt-1",
          auto_navigate_to_cart: false,
        }),
      }));

      const widget = document.createElement("assistant-chat-widget");
      document.body.appendChild(widget);
      const shadow = widget.shadowRoot!;
      const input = shadow.querySelector<HTMLInputElement>("input")!;
      const form = shadow.querySelector<HTMLFormElement>("form")!;
      input.value = "show me the classic t-shirt";
      form.dispatchEvent(new Event("submit", { cancelable: true }));

      await vi.waitFor(() => {
        expect(shadow.querySelectorAll(".message.assistant").length).toBe(1);
      });
      expect(setHref).not.toHaveBeenCalled();
    });

    it("auto-navigates to the cart page after a confirmed cart mutation", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          session_id: "s1",
          reply: "Added! Your cart now has 1 x Classic T-Shirt.",
          needs_confirmation: false,
          product_links: [],
          show_cart_link: true,
          auto_navigate_product_id: null,
          auto_navigate_to_cart: true,
        }),
      }));

      const widget = document.createElement("assistant-chat-widget");
      document.body.appendChild(widget);
      const shadow = widget.shadowRoot!;
      const input = shadow.querySelector<HTMLInputElement>("input")!;
      const form = shadow.querySelector<HTMLFormElement>("form")!;
      input.value = "yes";
      form.dispatchEvent(new Event("submit", { cancelable: true }));

      await vi.waitFor(() => {
        expect(setHref).toHaveBeenCalled();
      });
      expect(setHref).toHaveBeenCalledWith(`${originalLocation.origin}/index.php?controller=cart`);
    });

    it("does not auto-navigate to cart when already on the cart page", async () => {
      vi.stubGlobal("prestashop", { page: { page_name: "cart" } });
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          session_id: "s1",
          reply: "Added! Your cart now has 1 x Classic T-Shirt.",
          needs_confirmation: false,
          product_links: [],
          show_cart_link: true,
          auto_navigate_product_id: null,
          auto_navigate_to_cart: true,
        }),
      }));

      const widget = document.createElement("assistant-chat-widget");
      document.body.appendChild(widget);
      const shadow = widget.shadowRoot!;
      const input = shadow.querySelector<HTMLInputElement>("input")!;
      const form = shadow.querySelector<HTMLFormElement>("form")!;
      input.value = "yes";
      form.dispatchEvent(new Event("submit", { cancelable: true }));

      await vi.waitFor(() => {
        expect(shadow.querySelectorAll(".message.assistant").length).toBe(1);
      });
      expect(setHref).not.toHaveBeenCalled();
    });
  });

  it("restores the visible transcript after a simulated page navigation", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        session_id: "s1",
        reply: "Here's what I found: Classic T-Shirt ($19.99)",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const first = document.createElement("assistant-chat-widget");
    document.body.appendChild(first);
    const firstShadow = first.shadowRoot!;
    const input = firstShadow.querySelector<HTMLInputElement>("input")!;
    const form = firstShadow.querySelector<HTMLFormElement>("form")!;
    input.value = "show me t-shirts";
    form.dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => {
      expect(firstShadow.querySelectorAll(".message.assistant").length).toBe(1);
    });

    // A traditional storefront does a full page load on navigation — the old element (and
    // its DOM-only transcript) is gone, exactly like a real navigation would destroy it.
    // A fresh widget instance re-reads the SAME localStorage a real new page load would.
    first.remove();
    const second = document.createElement("assistant-chat-widget");
    document.body.appendChild(second);
    const secondShadow = second.shadowRoot!;

    expect(secondShadow.querySelector(".message.user")?.textContent).toContain("show me t-shirts");
    expect(secondShadow.querySelector(".message.assistant")?.textContent).toContain("Classic T-Shirt");
    // Restoring history must not re-fetch or re-save it as if these were new messages.
    expect(fetchMock).toHaveBeenCalledTimes(1);
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
