/** Minimal embeddable chat widget (T063): a single <assistant-chat-widget> custom element.
 *
 * Usage: `<script src="assistant-widget.js"></script>` then
 * `<assistant-chat-widget api-base="http://localhost:8000"></assistant-chat-widget>`.
 * No framework dependency, Shadow DOM for style isolation, so it can be embedded on any
 * page without clashing with the host site's CSS.
 */

import { sendChatMessage, type ProductLink } from "./api";

const CHAT_ICON = `<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>`;
const CLOSE_ICON = `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;

const STYLE = `
  :host {
    all: initial;
    position: fixed;
    bottom: 20px;
    right: 20px;
    z-index: 2147483647;
    font-family: system-ui, sans-serif;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
  }
  .launcher {
    flex: 0 0 auto;
    width: 56px;
    height: 56px;
    border-radius: 50%;
    border: none;
    background: #2563eb;
    color: #fff;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .launcher:hover { background: #1d4fd0; }
  .panel {
    display: none;
    flex-direction: column;
    width: 320px;
    height: 420px;
    margin-bottom: 12px;
    border-radius: 12px;
    background: #fff;
    box-shadow: 0 8px 30px rgba(0, 0, 0, 0.2);
    overflow: hidden;
    box-sizing: border-box;
  }
  :host([open]) .panel { display: flex; }
  .header {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: #2563eb;
    color: #fff;
    padding: 10px 12px;
    font-size: 14px;
    font-weight: 600;
  }
  .header button {
    background: none;
    border: none;
    color: #fff;
    cursor: pointer;
    display: flex;
    padding: 2px;
  }
  .messages { flex: 1; overflow-y: auto; padding: 8px; box-sizing: border-box; }
  .message { margin-bottom: 8px; padding: 8px; border-radius: 6px; white-space: pre-wrap; font-size: 14px; }
  .message.user { background: #e6f0ff; margin-left: 40px; text-align: right; }
  .message.assistant { background: #f2f2f2; margin-right: 40px; }
  .message.assistant.confirm { background: #fff6da; border: 1px solid #e0c46c; }
  .badge { display: block; font-size: 11px; font-weight: 600; color: #8a6d00; margin-bottom: 4px; }
  .links { margin-top: 6px; display: flex; flex-direction: column; gap: 4px; }
  .links a { color: #2563eb; font-size: 13px; text-decoration: none; }
  .links a:hover { text-decoration: underline; }
  form { flex: 0 0 auto; display: flex; border-top: 1px solid #ccc; }
  input { flex: 1; border: none; padding: 8px; font-size: 14px; outline: none; }
  button { border: none; background: #2563eb; color: #fff; padding: 0 16px; cursor: pointer; }
  button:disabled { background: #93b4f0; cursor: default; }
`;

function randomSessionId(): string {
  return `widget-${Math.random().toString(36).slice(2)}-${Date.now()}`;
}

interface StoredMessage {
  text: string;
  role: "user" | "assistant";
  confirm: boolean;
  productLinks?: ProductLink[];
  showCartLink?: boolean;
}

// A traditional server-rendered storefront (like PrestaShop) does a full page load on every
// navigation — the widget's whole DOM, including the chat panel, is destroyed and rebuilt
// from scratch. `sessionId` already survives that via localStorage (below), so the
// *backend's* cart/pending-action state was never actually lost — but the visibly rendered
// transcript was, since it only ever lived in the DOM. This persists it the same way.
const MAX_STORED_MESSAGES = 100;

export class AssistantChatWidget extends HTMLElement {
  private sessionId: string;
  private messagesEl!: HTMLDivElement;
  private inputEl!: HTMLInputElement;
  private buttonEl!: HTMLButtonElement;
  private messageHistory: StoredMessage[];

  constructor() {
    super();
    this.sessionId = this.getAttribute("session-id") || this.loadOrCreateSessionId();
    this.messageHistory = this.loadMessageHistory();
  }

  private loadOrCreateSessionId(): string {
    const storageKey = "assistant-widget-session-id";
    try {
      const stored = window.localStorage.getItem(storageKey);
      if (stored) return stored;
      const created = randomSessionId();
      window.localStorage.setItem(storageKey, created);
      return created;
    } catch {
      // localStorage unavailable (private mode, etc.) - a fresh id per page load is fine.
      return randomSessionId();
    }
  }

  private get historyStorageKey(): string {
    return `assistant-widget-messages-${this.sessionId}`;
  }

  private loadMessageHistory(): StoredMessage[] {
    try {
      const raw = window.localStorage.getItem(this.historyStorageKey);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      // Unavailable storage, or corrupted JSON from a previous version — start fresh
      // rather than let a restore failure break the widget from loading at all.
      return [];
    }
  }

  private saveMessageHistory(): void {
    try {
      const trimmed = this.messageHistory.slice(-MAX_STORED_MESSAGES);
      window.localStorage.setItem(this.historyStorageKey, JSON.stringify(trimmed));
    } catch {
      // Storage full/unavailable - the current page's transcript still renders fine;
      // only cross-navigation persistence is lost.
    }
  }

  connectedCallback(): void {
    const root = this.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = STYLE;

    const panel = document.createElement("div");
    panel.className = "panel";

    const header = document.createElement("div");
    header.className = "header";
    const title = document.createElement("span");
    title.textContent = "Chat with us";
    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.innerHTML = CLOSE_ICON;
    closeButton.setAttribute("aria-label", "Close chat");
    closeButton.addEventListener("click", () => this.setOpen(false));
    header.appendChild(title);
    header.appendChild(closeButton);

    this.messagesEl = document.createElement("div");
    this.messagesEl.className = "messages";
    for (const stored of this.messageHistory) {
      this.renderMessage(stored.text, stored.role, stored.confirm, stored.productLinks ?? [], stored.showCartLink ?? false);
    }

    const form = document.createElement("form");
    this.inputEl = document.createElement("input");
    this.inputEl.type = "text";
    this.inputEl.placeholder = "Ask about products, your cart...";
    this.buttonEl = document.createElement("button");
    this.buttonEl.type = "submit";
    this.buttonEl.textContent = "Send";

    form.appendChild(this.inputEl);
    form.appendChild(this.buttonEl);
    panel.appendChild(header);
    panel.appendChild(this.messagesEl);
    panel.appendChild(form);

    const launcher = document.createElement("button");
    launcher.type = "button";
    launcher.className = "launcher";
    launcher.innerHTML = CHAT_ICON;
    launcher.setAttribute("aria-label", "Open chat");
    launcher.addEventListener("click", () => this.setOpen(!this.hasAttribute("open")));

    root.appendChild(style);
    root.appendChild(panel);
    root.appendChild(launcher);

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void this.handleSend();
    });
  }

  private setOpen(open: boolean): void {
    if (open) {
      this.setAttribute("open", "");
      this.inputEl.focus();
    } else {
      this.removeAttribute("open");
    }
  }

  private get apiBase(): string {
    return this.getAttribute("api-base") || "http://localhost:8000";
  }

  // The public widget key a merchant's backoffice issues (backoffice/backend's
  // POST /tenants/{id}/widget-keys) — undefined when the embed doesn't set it, which
  // resolves to the legacy default tenant exactly as before multi-tenancy existed.
  private get tenantKey(): string | undefined {
    return this.getAttribute("tenant-key") || undefined;
  }

  // PrestaShop's own theme renders `window.prestashop.customer` for every front-office page
  // load — `.email` only, never a numeric id (PrestaShop's front-end deliberately doesn't
  // expose one to client-side JS). Present only for a genuinely logged-in shopper; undefined
  // for anonymous/guest browsing, matching the API's own optional customer_email field.
  private get customerEmail(): string | undefined {
    try {
      const ps = (window as unknown as { prestashop?: { customer?: { is_logged?: boolean; email?: string } } })
        .prestashop;
      return ps?.customer?.is_logged ? ps.customer.email || undefined : undefined;
    } catch {
      // window.prestashop absent entirely (widget embedded outside a PrestaShop page, e.g.
      // the e2e demo harness) — anonymous/guest behavior is the correct fallback either way.
      return undefined;
    }
  }

  /** DOM-only — renders one message bubble without touching stored history. Used both by
   * appendMessage() (a genuinely new message) and connectedCallback() (replaying history
   * already in storage, which must not be re-saved as if it were new). */
  private renderMessage(
    text: string,
    role: "user" | "assistant",
    confirm: boolean,
    productLinks: ProductLink[] = [],
    showCartLink = false,
  ): void {
    const el = document.createElement("div");
    el.className = `message ${role}${confirm ? " confirm" : ""}`;
    if (confirm) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "Needs your confirmation";
      el.appendChild(badge);
    }
    const textNode = document.createElement("span");
    textNode.textContent = text;
    el.appendChild(textNode);

    if (productLinks.length > 0 || showCartLink) {
      const links = document.createElement("div");
      links.className = "links";
      const origin = window.location.origin;
      for (const product of productLinks) {
        const a = document.createElement("a");
        // PrestaShop's stable, always-valid controller URL — works regardless of
        // friendly-URL/rewrite config, redirects to the real product page (verified
        // against a live store; no tenant-specific public URL needs configuring here).
        a.href = `${origin}/index.php?id_product=${encodeURIComponent(product.id)}&controller=product`;
        a.textContent = `View: ${product.name} →`;
        links.appendChild(a);
      }
      if (showCartLink) {
        const a = document.createElement("a");
        a.href = `${origin}/index.php?controller=cart`;
        a.textContent = "View my cart →";
        links.appendChild(a);
      }
      el.appendChild(links);
    }

    this.messagesEl.appendChild(el);
    this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
  }

  private appendMessage(
    text: string,
    role: "user" | "assistant",
    confirm = false,
    productLinks: ProductLink[] = [],
    showCartLink = false,
  ): void {
    this.renderMessage(text, role, confirm, productLinks, showCartLink);
    this.messageHistory.push({ text, role, confirm, productLinks, showCartLink });
    this.saveMessageHistory();
  }

  private async handleSend(): Promise<void> {
    const message = this.inputEl.value.trim();
    if (!message) return;

    this.appendMessage(message, "user");
    this.inputEl.value = "";
    this.inputEl.disabled = true;
    this.buttonEl.disabled = true;

    try {
      const { reply, needs_confirmation, product_links, show_cart_link } = await sendChatMessage(
        this.apiBase,
        this.sessionId,
        message,
        this.tenantKey,
        this.customerEmail,
      );
      this.appendMessage(reply, "assistant", needs_confirmation, product_links, show_cart_link);
    } catch {
      this.appendMessage(
        "Sorry, I couldn't reach the assistant service right now. Please try again.",
        "assistant",
      );
    } finally {
      this.inputEl.disabled = false;
      this.buttonEl.disabled = false;
      this.inputEl.focus();
    }
  }
}

if (!customElements.get("assistant-chat-widget")) {
  customElements.define("assistant-chat-widget", AssistantChatWidget);
}
