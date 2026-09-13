"""Redis-backed ConversationSession + PendingAction store (data-model.md, T013).

Falls back to an in-process in-memory store when Redis isn't reachable, so unit tests and
local dev don't hard-require a running Redis instance — but production/demo use should set
REDIS_URL (backend/.env.example) for real multi-process persistence.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field

try:
    import redis  # type: ignore
except ImportError:  # pragma: no cover - redis-py is a declared dependency, but keep this
    redis = None  # type: ignore


@dataclass
class PendingAction:
    """The structural confirm-before-mutate gate (Constitution Principle III).

    See research.md §9.3/§9.4: only `confirm_action()` in agent/pending.py may turn this
    into a real adapter mutation call; the LLM itself never has a tool that can do so.
    """

    action_id: str
    action_type: str  # add_cart_item | update_cart_item | remove_cart_item | apply_promo | checkout
    parameters: dict
    recap_text: str
    created_at: float
    confirmed: bool = False


@dataclass
class ConversationSession:
    session_id: str
    cart_id: str | None = None
    navigation_context: dict = field(default_factory=dict)
    # A compact text summary of the products from the most recent search/navigate result
    # (dialogue.py's _format_products — "Name ($price); ..."), fed back to the LLM as context
    # on the NEXT turn. Without this, a follow-up question like "does it fit a young man?"
    # right after a search result has zero connection to what was just shown — the LLM has
    # no way to know what "it" refers to, even though the shopper can see it right above.
    last_shown_products: str = ""
    # Ordered product ids backing last_shown_products (same list, same order) — lets a
    # follow-up "add it" / "the second one" resolve deterministically against exactly what
    # this shopper was just shown (agent/intents.py's _resolve_reference_to_last_shown),
    # rather than a fresh keyword search that has no idea what "it" refers to.
    last_shown_product_ids: list[str] = field(default_factory=list)
    # The real, logged-in shopper's email (widget-read from window.prestashop.customer.email
    # — see PrestaShopAdapter.set_customer_context's docstring for the trust model), or None
    # for an anonymous/guest session using the tenant's shared demo identity.
    real_customer_email: str | None = None
    # Reset every turn (agent/dialogue.py's _route_turn) — api/chat.py reads these right
    # after handle_turn returns to build ChatResponse.product_links/show_cart_link, the same
    # post-turn-session-read pattern needs_confirmation already uses.
    last_turn_product_ids: list[str] = field(default_factory=list)
    last_turn_product_names: list[str] = field(default_factory=list)
    last_turn_shows_cart_link: bool = False
    # Set only when exactly one product is unambiguously this turn's focus, or a cart
    # mutation was genuinely confirmed — the widget performs a real navigation for these
    # (unless window.prestashop.page says the shopper is already there), not just a link.
    last_turn_auto_navigate_product_id: str | None = None
    last_turn_auto_navigate_to_cart: bool = False
    # The instruction the widget must execute against the store's real front-office cart
    # endpoint for a just-confirmed mutation to actually take effect (client-cart-synced
    # sessions only — see client_cart_snapshot above); None otherwise.
    last_turn_client_cart_action: dict | None = None
    # Set only when checkout was just confirmed for a client-cart-synced session — the
    # widget navigates to the store's own real checkout page instead of chat having placed
    # an order directly (see PendingActionGate.confirm()'s checkout branch).
    last_turn_handoff_to_native_checkout: bool = False
    # True only when THIS turn's action handling actually (re-)presented a confirmation
    # prompt — a genuinely new/changed PendingAction — not merely "one happens to still
    # exist from an earlier, unrelated turn." Fixes a real, confirmed live bug:
    # needs_confirmation used to be `pending_action is not None`, which stayed True on
    # every turn after a proposal until explicitly resolved, even for a totally unrelated
    # reply in between (e.g. an off-topic question correctly declined by ask_or_chat still
    # rendered with the "needs your confirmation" badge). See _route_turn in dialogue.py.
    last_turn_needs_confirmation: bool = False
    # Persists across turns (unlike the last_turn_* fields above) until resolved — set
    # whenever resolve_add_to_cart lands on exactly one product but can't tell which variant
    # (AMBIGUOUS_VARIANT), cleared once that's answered or a clearly different flow starts.
    # Without this, a bare follow-up like "size S white" has no idea which product it's
    # answering for — dialogue.py._handle_propose_add_to_cart prioritizes this over
    # last_shown_product_ids so the answer resolves against the right item, not whatever was
    # last searched (which could be a stale, unrelated discovery result).
    pending_variant_product_id: str | None = None
    pending_variant_product_name: str = ""
    # Real, confirmed live bug (a full adversarial conversation transcript): a real hosted
    # LLM very frequently misclassified a bare or noisy reply naming a real size/color
    # ("size S", "S", "M", "ADD SWEATER SIZE S") as search_products/navigate_to instead of
    # continuing the add-to-cart flow — one specific manifestation, a bare "M" meant as
    # "size M", got routed to category search and matched "Home"/"Men"/"Women"/"Home
    # Accessories" (all literally contain the letter "m"). Combined with a bare "yes"
    # correctly but unhelpfully re-asking the same question (it carries no size info to
    # resolve with), the shopper was stuck in a loop unable to ever answer the question at
    # all. The real attribute VALUES of the pending product (e.g. ["S","M","L","XL"]),
    # captured once when the question is asked, let a deterministic override
    # (dialogue.py's _pending_variant_answer_override) route any later reply mentioning one
    # of them straight back to propose_add_to_cart — bypassing the LLM's classification
    # entirely for this narrow, high-stakes case, the same posture as every other bare-reply
    # override in this module.
    pending_variant_attribute_values: list[str] = field(default_factory=list)
    # Real, confirmed live bug: after an AMBIGUOUS_PRODUCT clarifying question ("did you
    # mean: Mountain fox notebook, Brown bear notebook, Hummingbird notebook?"), a real
    # hosted LLM inconsistently classified the shopper's answer ("brown bear one", "the
    # notebook") as search_products or ask_or_chat instead of continuing the add-to-cart
    # flow — forcing the shopper to repeat themselves several times even though the reply
    # named one of the candidates just offered. Mirrors pending_variant_product_id/_name's
    # role for the variant-level version of this same reliability gap: lets a deterministic
    # override (dialogue.py's _pending_product_clarify_override) route the very next turn
    # straight to propose_add_to_cart without needing the LLM to classify it correctly at
    # all. Cleared once resolved to a single product (or a clearly different flow starts).
    pending_product_clarify_ids: list[str] = field(default_factory=list)
    pending_product_clarify_names: list[str] = field(default_factory=list)
    pending_action: PendingAction | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # US4/data-model.md PromoStrategy `first_order` signal: this shopper session has not
    # yet completed a checkout. Flipped once by dialogue.py after a successful order — this
    # feature has no account/login system, so "first order" is scoped to this session.
    has_completed_order: bool = False
    # Real, confirmed live bug (adversarial review, specs/003-adversarial-qa-review): a chat
    # confirmation ("Your cart now has...") wrote to a webservice-created cart that PrestaShop's
    # OWN front-end session (the one the shopper's normal browsing cart/checkout live in) has
    # no way to know about — the shopper could add via chat, get told it worked, then see an
    # EMPTY cart on the store's own cart page. PrestaShop's front-end never exposes a raw
    # numeric cart id to JS (same reason real_customer_email above stores an email, not an
    # id) — this is the closest available "shared truth": the shopper's browser (same origin,
    # real session cookie) sends its OWN cart contents (window.prestashop.cart) with the chat
    # request; the backend treats this as ground truth for reading the cart, and expresses any
    # confirmed mutation as an instruction (dialogue.py's _client_cart_action) for the widget
    # to execute against the store's real front-office cart endpoint — never the reverse.
    client_cart_snapshot: list[dict] | None = None
    # Real, confirmed live bug: a synced cart's discount/voucher state is invisible to
    # cart_from_snapshot (it only ever knows about line items) — so a promo applied via
    # native checkout was never reflected back in chat, AND a promo applied via chat itself
    # looked freshly gone again on the very next turn (view_cart, a new suggestion) because
    # each turn rebuilds the Cart from the snapshot alone, with no memory of it. The widget
    # reads window.prestashop.cart.subtotals.discounts/vouchers — PrestaShop's own real,
    # already-computed totals — and reports it here every turn, the same "browser is the only
    # place with legitimate access to the real cart" trust model as client_cart_snapshot
    # above. {"code": str, "amount": float} or None when no discount is currently active.
    client_cart_discount: dict | None = None


class SessionStore:
    """Get/create sessions, read/write the one in-flight PendingAction per session."""

    DEFAULT_SESSION_TTL_SECONDS = 60 * 60  # 1 hour of shopper inactivity

    def __init__(self, redis_url: str | None = None, *, key_prefix: str = "") -> None:
        # key_prefix namespaces Redis keys per tenant (T204) — empty for the legacy
        # single-tenant/default deployment so its existing keys are unaffected.
        self._key_prefix = key_prefix
        self._redis_url = redis_url or os.environ.get("REDIS_URL")
        self._client = None
        if redis is not None and self._redis_url:
            try:
                self._client = redis.from_url(self._redis_url, decode_responses=True)
                self._client.ping()
            except Exception:  # noqa: BLE001 - fall back to in-memory store below
                self._client = None
        self._memory: dict[str, ConversationSession] = {}

    def _key(self, session_id: str) -> str:
        return f"{self._key_prefix}session:{session_id}"

    def get_or_create(self, session_id: str) -> ConversationSession:
        existing = self._read(session_id)
        if existing is not None:
            return existing
        session = ConversationSession(session_id=session_id)
        self._write(session)
        return session

    def save(self, session: ConversationSession) -> None:
        session.updated_at = time.time()
        self._write(session)

    def remember_cart_id(self, session: ConversationSession, cart_id: str) -> None:
        """Persists the adapter's real cart identifier onto the session once known.

        Real, confirmed live bug (found via adversarial review): PrestaShopAdapter maps its
        own session_id -> real PrestaShop cart id purely in-memory (`_cart_id_map`), and that
        map is rebuilt empty every time tenancy/runtime.py's short-TTL TenantRuntime cache
        rebuilds the adapter instance (every 60s) — after which the shopper's real cart
        becomes silently unreachable and a fresh, EMPTY one gets created on the next call,
        making a confirmed "Your cart now has..." turn into "Your cart is empty" minutes
        later. `ConversationSession.cart_id` exists specifically so `_cart_id_for` can hand
        the adapter a STABLE id across rebuilds instead of the volatile session_id — this is
        the one place that id actually gets written. A no-op once already up to date.
        """
        if cart_id and session.cart_id != cart_id:
            session.cart_id = cart_id
            self.save(session)

    def propose_action(
        self, session_id: str, action_type: str, parameters: dict, recap_text: str
    ) -> PendingAction:
        """Creates a new PendingAction, replacing/invalidating any prior one (research.md §9.4:
        a stray later 'yes' must never confirm a stale, no-longer-relevant proposal)."""
        session = self.get_or_create(session_id)
        action = PendingAction(
            action_id=str(uuid.uuid4()),
            action_type=action_type,
            parameters=parameters,
            recap_text=recap_text,
            created_at=time.time(),
            confirmed=False,
        )
        session.pending_action = action
        self.save(session)
        return action

    def confirm_action(self, session_id: str, action_id: str) -> PendingAction | None:
        """Marks the pending action confirmed IFF it matches action_id exactly — returns
        None if there is no matching pending action (e.g. it was invalidated/expired)."""
        session = self.get_or_create(session_id)
        action = session.pending_action
        if action is None or action.action_id != action_id:
            return None
        action.confirmed = True
        self.save(session)
        return action

    def clear_pending_action(self, session_id: str) -> None:
        """Invalidates the current pending action (decline, topic change, or post-execution)."""
        session = self.get_or_create(session_id)
        session.pending_action = None
        self.save(session)

    def _read(self, session_id: str) -> ConversationSession | None:
        if self._client is not None:
            raw = self._client.get(self._key(session_id))
            if raw is None:
                return None
            data = json.loads(raw)
            pending = data.get("pending_action")
            return ConversationSession(
                session_id=data["session_id"],
                cart_id=data.get("cart_id"),
                navigation_context=data.get("navigation_context", {}),
                last_shown_products=data.get("last_shown_products", ""),
                last_shown_product_ids=data.get("last_shown_product_ids", []),
                real_customer_email=data.get("real_customer_email"),
                last_turn_product_ids=data.get("last_turn_product_ids", []),
                last_turn_product_names=data.get("last_turn_product_names", []),
                last_turn_shows_cart_link=data.get("last_turn_shows_cart_link", False),
                last_turn_auto_navigate_product_id=data.get("last_turn_auto_navigate_product_id"),
                last_turn_auto_navigate_to_cart=data.get("last_turn_auto_navigate_to_cart", False),
                last_turn_client_cart_action=data.get("last_turn_client_cart_action"),
                last_turn_handoff_to_native_checkout=data.get("last_turn_handoff_to_native_checkout", False),
                last_turn_needs_confirmation=data.get("last_turn_needs_confirmation", False),
                pending_variant_product_id=data.get("pending_variant_product_id"),
                pending_variant_product_name=data.get("pending_variant_product_name", ""),
                pending_variant_attribute_values=data.get("pending_variant_attribute_values", []),
                pending_product_clarify_ids=data.get("pending_product_clarify_ids", []),
                pending_product_clarify_names=data.get("pending_product_clarify_names", []),
                pending_action=PendingAction(**pending) if pending else None,
                created_at=data.get("created_at", time.time()),
                updated_at=data.get("updated_at", time.time()),
                has_completed_order=data.get("has_completed_order", False),
                client_cart_snapshot=data.get("client_cart_snapshot"),
                client_cart_discount=data.get("client_cart_discount"),
            )
        return self._memory.get(session_id)

    def _write(self, session: ConversationSession) -> None:
        if self._client is not None:
            payload = asdict(session)
            self._client.set(
                self._key(session.session_id),
                json.dumps(payload),
                ex=self.DEFAULT_SESSION_TTL_SECONDS,
            )
        else:
            self._memory[session.session_id] = session
