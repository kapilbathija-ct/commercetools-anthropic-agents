"use client";

import { use, useEffect, useState } from "react";
import { formatMoney } from "web-shared";
import { api } from "@/lib/api";
import { type Confirmation, fetchConfirmation } from "@/lib/checkout";
import { useStoredSession } from "@/lib/session";

/**
 * The confirmation. The order is read back through the service, which scopes it to this
 * session's own principal -- reading an order straight off the URL id with no ownership
 * check is an IDOR, and it is a mistake this project has made once already.
 */
export default function ConfirmationPage({ params }: { params: Promise<{ orderId: string }> }) {
  const { orderId } = use(params);
  const session = useStoredSession(api);
  const [order, setOrder] = useState<Confirmation | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!session.ready) return;
    void (async () => {
      const loaded = await fetchConfirmation(orderId);
      if (loaded === null) setFailed(true);
      else setOrder(loaded);
    })();
  }, [session.ready, orderId]);

  if (failed) {
    return (
      <main className="mx-auto max-w-xl p-8">
        <h1 className="text-[22px] font-bold text-(--ink)">We can&apos;t show that order</h1>
        <p className="mt-2 text-[14px] text-(--ink-soft)">
          It may belong to another session. If you were charged, the order exists — check your
          email for the confirmation.
        </p>
      </main>
    );
  }

  if (!order) {
    return <main className="p-8 text-[14px] text-(--ink-soft)">Loading your order…</main>;
  }

  return (
    <main className="mx-auto max-w-xl p-8" data-testid="confirmation">
      <h1 className="text-[22px] font-bold text-(--ink)">Order confirmed</h1>
      <p className="mt-2 text-[14px] text-(--ink-soft)">
        Thank you. Your order{order.order_number ? ` ${order.order_number}` : ""} is placed.
      </p>
      <dl className="mt-5 grid grid-cols-2 gap-2 text-[13px]">
        <dt className="text-(--ink-soft)">Order id</dt>
        <dd className="text-(--ink)" data-testid="order-id">
          {order.order_id}
        </dd>
        <dt className="text-(--ink-soft)">Status</dt>
        <dd className="text-(--ink)">{order.state}</dd>
        <dt className="text-(--ink-soft)">Payment</dt>
        <dd className="text-(--ink)" data-testid="payment-state">
          {order.payment_state ?? "—"}
        </dd>
        <dt className="text-(--ink-soft)">Total charged</dt>
        <dd className="font-semibold text-(--ink)" data-testid="order-total">
          {formatMoney(order.total, order.currency)}
        </dd>
      </dl>
      <ul className="mt-5 grid gap-2 border-t border-(--line) pt-4 text-[14px] text-(--ink)">
        {order.items.map((item, index) => (
          <li key={`${item.title}-${index}`}>
            {item.title} × {item.quantity}
          </li>
        ))}
      </ul>
      <a href="/" className="mt-6 inline-block text-[14px] font-semibold text-(--accent)">
        Back to the store
      </a>
    </main>
  );
}
