/** Minimal embeddable chat widget (T063): a single <assistant-chat-widget> custom element.
 *
 * Usage: `<script src="assistant-widget.js"></script>` then
 * `<assistant-chat-widget api-base="http://localhost:8000"></assistant-chat-widget>`.
 * No framework dependency, Shadow DOM for style isolation, so it can be embedded on any
 * page without clashing with the host site's CSS.
 */

import { needsConfirmation, sendChatMessage } from "./api";

const STYLE = `
  :host { all: initial; }
  .widget {
    font-family: system-ui, sans-serif;
    border: 1px solid #ccc;
    border-radius: 8px;
    width: 320px;
    height: 420px;
    display: flex;
    flex-direction: column;
    background: #fff;
    box-sizing: border-box;
  }
  .messages { flex: 1; overflow-y: auto; padding: 8px; box-sizing: border-box; }
  .message { margin-bottom: 8px; padding: 8px; border-radius: 6px; white-space: pre-wrap; font-size: 14px; }
  .message.user { background: #e6f0ff; margin-left: 40px; text-align: right; }
  .message.assistant { background: #f2f2f2; margin-right: 40px; }
  .message.assistant.confirm { background: #fff6da; border: 1px solid #e0c46c; }
  .badge { display: block; font-size: 11px; font-weight: 600; color: #8a6d00; margin-bottom: 4px; }
  form { display: flex; border-top: 1px solid #ccc; }
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

    const wrapper = document.createElement("div");
    wrapper.className = "widget";

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
    wrapper.appendChild(this.messagesEl);
    wrapper.appendChild(form);
    root.appendChild(style);
    root.appendChild(wrapper);

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void this.handleSend();
    });
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
