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

  it("splits a proactive promo suggestion from an unrelated reply into separate bubbles", async () => {
    // Regression test for a real, confirmed live bug: a promo suggestion appended to a
    // substantial, unrelated reply (e.g. a multi-product search result) rendered as ONE
    // "Needs your confirmation" bubble containing both — visually implying the whole thing,
    // products included, needed a yes/no answer. dialogue.py joins the two parts with a
    // blank line specifically so the widget can tell them apart and only badge the part that
    // actually needs a yes/no; product links belong to the first (unrelated) part only.
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        session_id: "s1",
        reply:
          "Here's what I found: Classic T-Shirt ($19.99); Blue Jacket ($89.99).\n\n" +
          "Your cart now has: 1 x Classic T-Shirt ($19.99). Subtotal: $19.99. You qualify " +
          "for code WELCOME10, which would save you $2.00. Apply it to your cart? " +
          "(reply 'yes' to confirm or 'no' to cancel)",
        needs_confirmation: true,
        product_links: [
          { id: "prod-tshirt-1", name: "Classic T-Shirt" },
          { id: "prod-jacket-1", name: "Blue Jacket" },
        ],
        show_cart_link: false,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;
    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "show me shirts and jackets";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => {
      expect(shadow.querySelectorAll(".message.assistant")).toHaveLength(2);
    });
    const [first, second] = shadow.querySelectorAll(".message.assistant");
    expect(first.textContent).toContain("Here's what I found");
    expect(first.classList.contains("confirm")).toBe(false);
    expect(first.querySelectorAll(".links a")).toHaveLength(2);

    expect(second.textContent).toContain("WELCOME10");
    expect(second.classList.contains("confirm")).toBe(true);
    expect(second.querySelector(".badge")?.textContent).toContain("confirmation");
    expect(second.querySelectorAll(".links a")).toHaveLength(0);
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

    let reloadSpy: ReturnType<typeof vi.fn>;

    beforeEach(() => {
      // jsdom doesn't implement real navigation — replace window.location with a spy-able
      // stand-in so we can assert what the widget tried to navigate to, without jsdom's
      // "Not implemented: navigation" error.
      originalLocation = window.location;
      setHref = vi.fn();
      reloadSpy = vi.fn();
      Object.defineProperty(window, "location", {
        configurable: true,
        value: { origin: originalLocation.origin, set href(v: string) { setHref(v); }, reload: reloadSpy },
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

    it("reloads the page when a confirmed cart mutation lands while already on the cart page", async () => {
      // Regression test for a real, confirmed live bug: removing an item via chat while
      // already looking at the store's own cart page genuinely succeeds (the real
      // front-office write goes through), but that page is static server-rendered HTML —
      // its header badge, line items, and totals stay stale until something refreshes it.
      // The "navigate to cart" path above only fires when NOT already there, so this exact
      // case needs its own explicit reload.
      vi.stubGlobal("prestashop", { page: { page_name: "cart" }, cart: { products: [] } });
      const fetchMock = vi.fn().mockImplementation((url: string) => {
        if (String(url).includes("controller=cart")) {
          return Promise.resolve({ ok: true, json: async () => ({ success: true }) });
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({
            session_id: "s1",
            reply: "Your cart is now empty.",
            needs_confirmation: false,
            cart_action: { op: "remove", variant_id: "18#36" },
          }),
        });
      });
      vi.stubGlobal("fetch", fetchMock);

      const widget = document.createElement("assistant-chat-widget");
      document.body.appendChild(widget);
      const shadow = widget.shadowRoot!;
      const input = shadow.querySelector<HTMLInputElement>("input")!;
      const form = shadow.querySelector<HTMLFormElement>("form")!;
      input.value = "yes";
      form.dispatchEvent(new Event("submit", { cancelable: true }));

      await vi.waitFor(() => expect(reloadSpy).toHaveBeenCalled());
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

  // -- Adversarial UX review findings (specs/003-adversarial-qa-review) ------------------ //

  it("shows a typing indicator while waiting, and removes it once the real reply lands", async () => {
    let resolveFetch!: (value: unknown) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(
        new Promise((resolve) => {
          resolveFetch = resolve;
        }),
      ),
    );

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;
    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "hello";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => {
      expect(shadow.querySelector(".message.typing")).not.toBeNull();
    });
    // The transient typing bubble must never be mistaken for a real, landed reply.
    expect(shadow.querySelector(".message.assistant")).toBeNull();

    resolveFetch({
      ok: true,
      json: async () => ({ session_id: "s1", reply: "Here's what I found: shoes", needs_confirmation: false }),
    });

    await vi.waitFor(() => {
      expect(shadow.querySelector(".message.assistant")?.textContent).toContain("shoes");
    });
    expect(shadow.querySelector(".message.typing")).toBeNull();
  });

  it("has accessible names/roles for the input, message log, and panel", () => {
    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;

    expect(shadow.querySelector("input")?.getAttribute("aria-label")).toBe("Message");
    expect(shadow.querySelector(".messages")?.getAttribute("role")).toBe("log");
    expect(shadow.querySelector(".messages")?.getAttribute("aria-live")).toBe("polite");
    expect(shadow.querySelector(".panel")?.getAttribute("role")).toBe("dialog");
    expect(shadow.querySelector(".launcher")?.getAttribute("aria-expanded")).toBe("false");
  });

  it("toggles aria-expanded and returns focus to the launcher when closed", () => {
    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;
    const launcher = shadow.querySelector<HTMLButtonElement>(".launcher")!;
    const closeButton = shadow.querySelector<HTMLButtonElement>(".header button")!;

    launcher.click();
    expect(widget.hasAttribute("open")).toBe(true);
    expect(launcher.getAttribute("aria-expanded")).toBe("true");

    closeButton.click();
    expect(widget.hasAttribute("open")).toBe(false);
    expect(launcher.getAttribute("aria-expanded")).toBe("false");
    expect(shadow.activeElement).toBe(launcher);
  });

  it("restores the open/closed state across a simulated page navigation", () => {
    const first = document.createElement("assistant-chat-widget");
    document.body.appendChild(first);
    const firstShadow = first.shadowRoot!;
    firstShadow.querySelector<HTMLButtonElement>(".launcher")!.click();
    expect(first.hasAttribute("open")).toBe(true);

    // Simulates the real page-load-on-navigation this is meant to survive (see the existing
    // transcript-restore test above) — a fresh element re-reading the same localStorage.
    first.remove();
    const second = document.createElement("assistant-chat-widget");
    document.body.appendChild(second);

    expect(second.hasAttribute("open")).toBe(true);
  });

  it("stays closed by default for a session that never opened the panel", () => {
    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);

    expect(widget.hasAttribute("open")).toBe(false);
  });

  it("times out a hung request instead of leaving the input disabled forever", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((_url: string, init: RequestInit) => {
        // A backend that hangs (never resolves, never rejects on its own) rather than one
        // that errors quickly — only the AbortSignal firing settles this promise, exactly
        // like a real hung request would behave once aborted.
        return new Promise((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        });
      }),
    );

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;
    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "hello";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    expect(input.disabled).toBe(true);
    await vi.advanceTimersByTimeAsync(20_000);

    expect(shadow.querySelector(".message.assistant")?.textContent).toContain("couldn't reach");
    expect(input.disabled).toBe(false);
    vi.useRealTimers();
  });

  // -- Client-cart-sync (specs/003-adversarial-qa-review) ------------------------------ //
  //
  // Regression coverage for a real bug found via adversarial review: confirming an add via
  // chat never showed up on the store's own cart page — the chatbot's cart and PrestaShop's
  // real front-end session cart were completely disconnected. These tests confirm the
  // widget reports its real cart to the backend, and executes any resulting instruction
  // against PrestaShop's own front-office cart endpoint.

  function mockFetchRoutedByUrl(handlers: Record<string, () => unknown>) {
    return vi.fn().mockImplementation((url: string) => {
      for (const [match, handler] of Object.entries(handlers)) {
        if (url.includes(match)) {
          return Promise.resolve({ ok: true, json: async () => handler() });
        }
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
  }

  it("reports the real PrestaShop cart contents with every chat request", async () => {
    vi.stubGlobal("prestashop", {
      cart: { products: [{ id_product: "18", id_product_attribute: "36", quantity: 2 }] },
    });
    const fetchMock = mockFetchRoutedByUrl({
      "/chat": () => ({ session_id: "s1", reply: "Here's what I found: shoes", needs_confirmation: false }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;
    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "show me shoes";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, requestInit] = fetchMock.mock.calls[0];
    expect(JSON.parse(requestInit.body).cart_snapshot).toEqual([{ variant_id: "18#36", quantity: 2 }]);
  });

  it("omits cart_snapshot entirely when window.prestashop.cart is unavailable", async () => {
    const fetchMock = mockFetchRoutedByUrl({
      "/chat": () => ({ session_id: "s1", reply: "Here's what I found: shoes", needs_confirmation: false }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;
    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "show me shoes";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, requestInit] = fetchMock.mock.calls[0];
    expect(JSON.parse(requestInit.body).cart_snapshot).toBeUndefined();
  });

  // Regression coverage for a real, confirmed live bug: "what materials is this shirt made
  // of?" asked while standing right on that shirt's real storefront page got an ambiguous
  // multi-product reply instead of an answer about the shirt actually on screen — the
  // backend had no idea which product page the shopper was on. current_product_id is the
  // widget's side of the fix (see api/chat.py's ChatRequest.current_product_id and
  // DiscoveryIntentHandler.resolve_product_details).

  it("reports the current product page id with every chat request", async () => {
    vi.stubGlobal("prestashop", {
      page: { page_name: "product", body_classes: { "product-id-42": true } },
    });
    const fetchMock = mockFetchRoutedByUrl({
      "/chat": () => ({ session_id: "s1", reply: "It's 100% cotton.", needs_confirmation: false }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;
    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "what materials is this shirt made of?";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, requestInit] = fetchMock.mock.calls[0];
    expect(JSON.parse(requestInit.body).current_product_id).toBe("42");
  });

  it("omits current_product_id entirely when not on a product page", async () => {
    vi.stubGlobal("prestashop", { page: { page_name: "cart" } });
    const fetchMock = mockFetchRoutedByUrl({
      "/chat": () => ({ session_id: "s1", reply: "Here's what I found: shoes", needs_confirmation: false }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;
    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "show me shoes";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, requestInit] = fetchMock.mock.calls[0];
    expect(JSON.parse(requestInit.body).current_product_id).toBeUndefined();
  });

  it("executes a confirmed add against PrestaShop's real front-office cart endpoint", async () => {
    vi.stubGlobal("prestashop", { cart: { products: [] } });
    const fetchMock = mockFetchRoutedByUrl({
      "/chat": () => ({
        session_id: "s1",
        reply: "Your cart now has: 1 x Hummingbird notebook.",
        needs_confirmation: false,
        cart_action: { op: "increment", variant_id: "18#36", quantity: 1 },
      }),
      "controller=cart": () => ({ success: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;
    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "yes";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [cartUrl, cartInit] = fetchMock.mock.calls[1];
    expect(cartUrl).toContain("controller=cart");
    const params = new URLSearchParams(cartInit.body as string);
    expect(params.get("add")).toBe("1");
    expect(params.get("id_product")).toBe("18");
    expect(params.get("id_product_attribute")).toBe("36");
    expect(params.get("qty")).toBe("1");
    expect(params.get("op")).toBeNull(); // default "up" (increment)
  });

  it("computes a decrement from the current real quantity for a 'set' action", async () => {
    vi.stubGlobal("prestashop", { cart: { products: [{ id_product: "18", id_product_attribute: "36", quantity: 5 }] } });
    const fetchMock = mockFetchRoutedByUrl({
      "/chat": () => ({
        session_id: "s1",
        reply: "Your cart now has: 2 x Hummingbird notebook.",
        needs_confirmation: false,
        cart_action: { op: "set", variant_id: "18#36", quantity: 2 },
      }),
      "controller=cart": () => ({ success: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;
    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "yes";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [, cartInit] = fetchMock.mock.calls[1];
    const params = new URLSearchParams(cartInit.body as string);
    expect(params.get("op")).toBe("down");
    expect(params.get("qty")).toBe("3"); // 5 -> 2 is a decrement of 3
  });

  it("issues a delete for a 'remove' action, with no quantity math", async () => {
    vi.stubGlobal("prestashop", { cart: { products: [{ id_product: "18", id_product_attribute: "36", quantity: 5 }] } });
    const fetchMock = mockFetchRoutedByUrl({
      "/chat": () => ({
        session_id: "s1",
        reply: "Removed.",
        needs_confirmation: false,
        cart_action: { op: "remove", variant_id: "18#36" },
      }),
      "controller=cart": () => ({ success: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;
    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "yes";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [, cartInit] = fetchMock.mock.calls[1];
    const params = new URLSearchParams(cartInit.body as string);
    expect(params.get("delete")).toBe("1");
  });

  it("applies a confirmed promo code via the real front-office discount endpoint", async () => {
    // Regression coverage: applying a promo for a synced session used to be declined
    // outright ("I can't apply a discount code from chat..."); it's now a genuine write to
    // PrestaShop's real cart via the same controller=cart AJAX endpoint cart mutations
    // already use, verified against CartController.php's own addDiscount branch.
    vi.stubGlobal("prestashop", { cart: { products: [] } });
    const fetchMock = mockFetchRoutedByUrl({
      "/chat": () => ({
        session_id: "s1",
        reply: "Applied WELCOME10 — subtotal $19.12, discount -$1.91, total $17.21.",
        needs_confirmation: false,
        cart_action: { op: "apply_promo", code: "WELCOME10" },
      }),
      "controller=cart": () => ({ success: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;
    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "yes";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [cartUrl, cartInit] = fetchMock.mock.calls[1];
    expect(cartUrl).toContain("controller=cart");
    const params = new URLSearchParams(cartInit.body as string);
    expect(params.get("addDiscount")).toBe("1");
    expect(params.get("discount_name")).toBe("WELCOME10");
    expect(params.get("add")).toBeNull();
    expect(params.get("delete")).toBeNull();
  });

  it("shows an error and does not navigate when the real cart write fails", async () => {
    vi.stubGlobal("prestashop", { cart: { products: [] } });
    const fetchMock = mockFetchRoutedByUrl({
      "/chat": () => ({
        session_id: "s1",
        reply: "Your cart now has: 1 x Hummingbird notebook.",
        needs_confirmation: false,
        cart_action: { op: "increment", variant_id: "18#36", quantity: 1 },
        auto_navigate_to_cart: true,
      }),
      "controller=cart": () => ({ success: false, errors: ["out of stock"] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;
    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "yes";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => {
      expect(shadow.querySelectorAll(".message.assistant")).toHaveLength(2);
    });
    expect(shadow.querySelectorAll(".message.assistant")[1].textContent).toContain("couldn't update your actual cart");
  });

  it("navigates to the store's real checkout page on a synced checkout handoff", async () => {
    const originalLocation = window.location;
    const setHref = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { origin: originalLocation.origin, set href(v: string) { setHref(v); } },
    });

    vi.stubGlobal("prestashop", { urls: { pages: { order: "https://shop.example/order" } } });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          session_id: "s1",
          reply: "Taking you to checkout to complete your order.",
          needs_confirmation: false,
          auto_navigate_to_checkout: true,
        }),
      }),
    );

    const widget = document.createElement("assistant-chat-widget");
    document.body.appendChild(widget);
    const shadow = widget.shadowRoot!;
    const input = shadow.querySelector<HTMLInputElement>("input")!;
    const form = shadow.querySelector<HTMLFormElement>("form")!;
    input.value = "yes";
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => expect(setHref).toHaveBeenCalledWith("https://shop.example/order"));

    Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
  });
});
