"use client";

import { useEffect, useRef, useState } from "react";
import { paymentFlow } from "@commercetools/checkout-browser-sdk";
import type { CheckoutSession } from "@/lib/checkout";

/**
 * The real commercetools Checkout payment step, PaymentOnly mode. `paymentFlow` mounts the
 * widget into the `data-ctc` element and Checkout creates the Payment and the Order itself
 * on success -- there is no place-order call here.
 *
 * Two things about this are load-bearing rather than boilerplate:
 *
 * `paymentFlow` does not navigate on success. `skipPaymentSuccessPage` and
 * `skipPaymentErrorPage` default to true in PaymentOnly mode, and for an inline method like
 * a card nothing about the page changes once the charge succeeds. Without the
 * `checkout_completed` handler below, the card is really charged and the order really
 * created while the UI shows neither success nor error -- indistinguishable from a hang.
 * The message sequence is order_verification_started, payment_verification_started
 * (deprecated), order_created, then checkout_completed, with `payload.order.id` on the
 * last two.
 *
 * The `data-ctc` mount point must exist, or Checkout renders full-screen instead of inline.
 */
export default function PaymentStep({
  start,
}: {
  start: () => Promise<CheckoutSession | null>;
}) {
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const initialized = useRef(false);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;

    void (async () => {
      const session = await start();
      if (!session) {
        setError("Payment could not be started. Your cart may have changed — reload and retry.");
        setStatus("error");
        return;
      }
      paymentFlow({
        projectKey: session.projectKey,
        region: session.region,
        sessionId: session.sessionId,
        locale: "en-US",
        onInfo: (message) => {
          // Surfaced in the DOM so an automated test can assert the real sequence rather
          // than inferring it from a screenshot.
          setLog((previous) => [...previous, message.code]);
          if (message.code === "checkout_completed" || message.code === "order_created") {
            const orderId = (message.payload as { order?: { id?: string } })?.order?.id;
            if (orderId) {
              window.location.href = `/checkout/confirmation/${orderId}`;
              return;
            }
          }
          setStatus("ready");
        },
        onWarn: (message) => {
          setLog((previous) => [...previous, `warn:${message.code}`]);
          setStatus("ready");
        },
        onError: (message) => {
          const detail = (message.payload as { message?: string })?.message;
          setLog((previous) => [...previous, `error:${message.code}`]);
          setError(detail || "Payment failed. Please try again.");
          setStatus("error");
        },
      });
      // The widget mounts asynchronously with no "mounted" signal of its own, so the
      // spinner is cleared on a timer rather than waiting for a message that may not come.
      setTimeout(() => setStatus((current) => (current === "loading" ? "ready" : current)), 3000);
    })();
  }, [start]);

  return (
    <div className="mt-5">
      {status === "loading" && (
        <p className="py-4 text-[14px] text-(--ink-soft)">Loading the payment form…</p>
      )}

      {/* Required mount point. Without it Checkout renders full-screen. */}
      <div data-ctc />

      {error && (
        <p role="alert" className="mt-3 text-[13px] text-(--warn)">
          {error}
        </p>
      )}

      {/* A test hook, not chrome: the real onInfo sequence, in order. */}
      <p data-testid="checkout-messages" className="sr-only">
        {log.join(" ")}
      </p>
    </div>
  );
}
