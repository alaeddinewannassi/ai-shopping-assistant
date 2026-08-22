/** Minimal embeddable chat widget (T063): a single <assistant-chat-widget> custom element.
 *
 * Usage: `<script src="assistant-widget.js"></script>` then
 * `<assistant-chat-widget api-base="http://localhost:8000"></assistant-chat-widget>`.
 * No framework dependency, Shadow DOM for style isolation, so it can be embedded on any
 * page without clashing with the host site's CSS.
 */

import { needsConfirmation, sendChatMessage } from "./api";

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
  form { flex: 0 0 auto; display: flex; border-top: 1px solid #ccc; }
  input { flex: 1; border: none; padding: 8px; font-size: 14px; outline: none; }
  button { border: none; background: #2563eb; color: #fff; padding: 0 16px; cursor: pointer; }
  button:disabled { background: #93b4f0; cursor: default; }
`;

function randomSessionId(): string {
  return `widget-${Math.random().toString(36).slice(2)}-${Date.now()}`;
}

export class AssistantChatWidget extends HTMLElement {
  private sessionId: string;
  private messagesEl!: HTMLDivElement;
  private inputEl!: HTMLInputElement;
  private buttonEl!: HTMLButtonElement;

  constructor() {
    super();
    this.sessionId = this.getAttribute("session-id") || this.loadOrCreateSessionId();
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

  private appendMessage(text: string, role: "user" | "assistant", confirm = false): void {
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
    this.messagesEl.appendChild(el);
    this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
  }

  private async handleSend(): Promise<void> {
    const message = this.inputEl.value.trim();
    if (!message) return;

    this.appendMessage(message, "user");
    this.inputEl.value = "";
    this.inputEl.disabled = true;
    this.buttonEl.disabled = true;

    try {
      const { reply } = await sendChatMessage(this.apiBase, this.sessionId, message);
      this.appendMessage(reply, "assistant", needsConfirmation(reply));
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
