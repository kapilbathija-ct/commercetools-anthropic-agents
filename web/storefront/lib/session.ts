"use client";

import { useEffect, useState } from "react";
import type { AgentApi } from "web-shared";

/**
 * The session id, persisted across page loads.
 *
 * web-shared's own `useSession` starts a fresh session on every mount, which is right for a
 * single-page demo and wrong the moment there is a second route: a new session means a new
 * anonymous id, which means commercetools hands back a different cart, so navigating to
 * /checkout would arrive with an empty one. Persisting the id is also just what a real
 * storefront does with its session cookie.
 *
 * A stored id can be stale (the service restarted, or the session expired), so it is
 * validated once against a scoped route before being trusted; a 401 starts a fresh session.
 */
const STORAGE_KEY = "ct-agent-session";

/** What the shop calls the visitor until real sign-in replaces it. */
const SHOPPER_NAME = "Kapil";

/** web-shared's own `Session` shape, plus `ready` so a second route can wait for it. */
export interface StoredSession {
  sessionId: string | null;
  guest: boolean;
  shopper?: { name: string; tier?: string };
  ready: boolean;
}

export function useStoredSession(api: AgentApi): StoredSession {
  const [session, setSession] = useState<StoredSession>({
    sessionId: null,
    guest: true,
    ready: false,
  });

  useEffect(() => {
    let cancelled = false;

    async function start() {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored) {
        api.session = stored;
        // Any scoped route works as a liveness check; the cart is the cheapest.
        const alive = await api.get<unknown>("/cart");
        if (alive !== null && !cancelled) {
          setSession({ sessionId: stored, guest: true, shopper: { name: SHOPPER_NAME }, ready: true });
          return;
        }
      }
      const started = await api.startSession();
      if (cancelled) return;
      const id = started?.sessionId ?? null;
      api.session = id;
      if (id) window.localStorage.setItem(STORAGE_KEY, id);
      setSession({
        sessionId: id,
        guest: true,
        shopper: { name: started?.shopper?.name ?? SHOPPER_NAME },
        ready: true,
      });
    }

    void start();
    return () => {
      cancelled = true;
    };
  }, [api]);

  return session;
}

export function clearStoredSession(): void {
  window.localStorage.removeItem(STORAGE_KEY);
}
