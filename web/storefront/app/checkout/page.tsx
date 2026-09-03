"use client";

import { useCallback, useEffect, useState } from "react";
import { formatMoney } from "web-shared";
import { api, UNREACHABLE } from "@/lib/api";
import {
  type CheckoutAddress,
  type ShippingMethod,
  chooseShippingMethod,
  saveAddress,
  startPayment,
} from "@/lib/checkout";
import { useStoredSession } from "@/lib/session";
import type { CartPayload } from "@/lib/types";
import PaymentStep from "./PaymentStep";

type Step = "address" | "shipping" | "payment";

const BLANK: CheckoutAddress = {
  first_name: "",
  last_name: "",
  email: "",
  street_name: "",
  city: "",
  state: "",
  postal_code: "",
  country: "US",
};

function Field({
  label,
  value,
  onChange,
  required = true,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  required?: boolean;
  type?: string;
}) {
  const id = `field-${label.toLowerCase().replace(/\s+/g, "-")}`;
  return (
    <label htmlFor={id} className="flex flex-col gap-1 text-[13px] font-medium text-(--ink-soft)">
      {label}
      <input
        id={id}
        name={id}
        type={type}
        required={required}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="rounded-lg border border-(--line) bg-(--surface) px-3 py-2 text-[14px] text-(--ink) outline-none focus:border-(--accent)"
      />
    </label>
  );
}

export default function CheckoutPage() {
  const session = useStoredSession(api);
  const [cart, setCart] = useState<CartPayload | null>(null);
  const [step, setStep] = useState<Step>("address");
  const [address, setAddress] = useState<CheckoutAddress>(BLANK);
  const [methods, setMethods] = useState<ShippingMethod[]>([]);
  const [chosen, setChosen] = useState<string | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!session.ready || !session.sessionId) return;
    void (async () => {
      const loaded = await api.get<CartPayload>("/cart");
      if (loaded === null) setError(UNREACHABLE);
      else setCart(loaded);
    })();
  }, [session.ready, session.sessionId]);

  const submitAddress = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setBusy(true);
      setError(null);
      const saved = await saveAddress(address);
      setBusy(false);
      if (!saved) {
        setError("That address could not be saved. Check the fields and try again.");
        return;
      }
      setMethods(saved.shipping_methods);
      setStep("shipping");
    },
    [address],
  );

  const submitShipping = useCallback(async () => {
    if (!chosen) return;
    setBusy(true);
    setError(null);
    const result = await chooseShippingMethod(chosen);
    setBusy(false);
    if (!result) {
      setError("That delivery method could not be applied.");
      return;
    }
    setTotal(result.total);
    setStep("payment");
  }, [chosen]);

  if (!session.ready) {
    return <main className="p-8 text-[14px] text-(--ink-soft)">Starting your session…</main>;
  }

  if (cart && cart.items.length === 0) {
    return (
      <main className="mx-auto max-w-xl p-8">
        <h1 className="text-[22px] font-bold text-(--ink)">Your cart is empty</h1>
        <p className="mt-2 text-[14px] text-(--ink-soft)">
          Ask the assistant for something first, then come back here to check out.
        </p>
        <a href="/" className="mt-4 inline-block text-[14px] font-semibold text-(--accent)">
          Back to the store
        </a>
      </main>
    );
  }

  return (
    <main className="mx-auto grid max-w-5xl gap-8 p-6 sm:grid-cols-[1fr_320px]">
      <div>
        <h1 className="text-[22px] font-bold text-(--ink)">Checkout</h1>
        <ol className="mt-1 flex gap-3 text-[12px] text-(--ink-soft)">
          {(["address", "shipping", "payment"] as Step[]).map((name) => (
            <li key={name} className={step === name ? "font-semibold text-(--accent)" : ""}>
              {name === "address" ? "1. Address" : name === "shipping" ? "2. Delivery" : "3. Payment"}
            </li>
          ))}
        </ol>

        {step === "address" && (
          <form onSubmit={submitAddress} className="mt-5 grid gap-3 sm:grid-cols-2">
            <Field label="First name" value={address.first_name} onChange={(v) => setAddress({ ...address, first_name: v })} />
            <Field label="Last name" value={address.last_name} onChange={(v) => setAddress({ ...address, last_name: v })} />
            <Field label="Email" type="email" value={address.email} onChange={(v) => setAddress({ ...address, email: v })} />
            <Field label="Street address" value={address.street_name} onChange={(v) => setAddress({ ...address, street_name: v })} />
            <Field label="City" value={address.city} onChange={(v) => setAddress({ ...address, city: v })} />
            <Field label="State" required={false} value={address.state ?? ""} onChange={(v) => setAddress({ ...address, state: v })} />
            <Field label="Postal code" value={address.postal_code} onChange={(v) => setAddress({ ...address, postal_code: v })} />
            <Field label="Country" value={address.country} onChange={(v) => setAddress({ ...address, country: v.toUpperCase() })} />
            <div className="sm:col-span-2">
              <button
                type="submit"
                disabled={busy}
                className="rounded-lg bg-(--accent) px-4 py-2 text-[14px] font-semibold text-white disabled:opacity-50"
              >
                {busy ? "Saving…" : "Save address"}
              </button>
            </div>
          </form>
        )}

        {step === "shipping" && (
          <div className="mt-5">
            <fieldset>
              <legend className="text-[13px] font-medium text-(--ink-soft)">Delivery method</legend>
              <div className="mt-2 grid gap-2">
                {methods.map((method) => (
                  <label
                    key={method.id}
                    className="flex items-center gap-3 rounded-lg border border-(--line) bg-(--surface) px-3 py-2 text-[14px]"
                  >
                    <input
                      type="radio"
                      name="shipping-method"
                      value={method.id}
                      checked={chosen === method.id}
                      onChange={() => setChosen(method.id)}
                    />
                    <span className="flex-1 text-(--ink)">{method.name}</span>
                    <span className="text-(--ink-soft)">
                      {method.fee === 0 ? "Free" : formatMoney(method.fee, cart?.currency ?? "USD")}
                      {method.note ? ` · ${method.note}` : ""}
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
            <button
              type="button"
              onClick={submitShipping}
              disabled={!chosen || busy}
              className="mt-4 rounded-lg bg-(--accent) px-4 py-2 text-[14px] font-semibold text-white disabled:opacity-50"
            >
              {busy ? "Applying…" : "Continue to payment"}
            </button>
          </div>
        )}

        {step === "payment" && <PaymentStep start={startPayment} />}

        {error && (
          <p role="alert" className="mt-4 text-[13px] text-(--warn)">
            {error}
          </p>
        )}
      </div>

      <aside className="rounded-xl border border-(--line) bg-(--surface) p-4">
        <h2 className="text-[15px] font-semibold text-(--ink)">Order summary</h2>
        <ul className="mt-3 grid gap-2 text-[13px] text-(--ink)">
          {(cart?.items ?? []).map((item) => (
            <li key={item.product_id} className="flex justify-between gap-3">
              <span className="min-w-0 truncate">
                {item.title} × {item.quantity}
              </span>
              <span className="shrink-0 text-(--ink-soft)">
                {formatMoney(item.price * item.quantity, cart?.currency ?? "USD")}
              </span>
            </li>
          ))}
        </ul>
        <div className="mt-4 flex justify-between border-t border-(--line) pt-3 text-[14px] font-semibold text-(--ink)">
          <span>{total === null ? "Subtotal" : "Total"}</span>
          <span>{formatMoney(total ?? cart?.subtotal ?? 0, cart?.currency ?? "USD")}</span>
        </div>
        {total === null && (
          <p className="mt-1 text-[12px] text-(--ink-soft)">Delivery is added at the next step.</p>
        )}
      </aside>
    </main>
  );
}
