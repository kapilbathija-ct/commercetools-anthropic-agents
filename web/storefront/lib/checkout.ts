import { api } from "./api";

export interface ShippingMethod {
  id: string;
  name: string;
  description?: string | null;
  fee: number;
  note?: string | null;
}

export interface CheckoutAddress {
  first_name: string;
  last_name: string;
  email: string;
  street_name: string;
  city: string;
  state?: string;
  postal_code: string;
  country: string;
}

export interface CheckoutSession {
  sessionId: string;
  projectKey: string;
  region: string;
}

export interface Confirmation {
  order_id: string;
  order_number?: string | null;
  state?: string | null;
  payment_state?: string | null;
  total: number;
  currency: string;
  items: { title: string; quantity: number }[];
}

export async function saveAddress(
  address: CheckoutAddress,
): Promise<{ shipping_methods: ShippingMethod[] } | null> {
  return api.post<{ shipping_methods: ShippingMethod[] }>("/checkout/address", address);
}

export async function chooseShippingMethod(
  id: string,
): Promise<{ total: number; currency: string } | null> {
  return api.post<{ total: number; currency: string }>("/checkout/shipping-method", {
    shipping_method_id: id,
  });
}

/** Created only when the shopper reaches the payment step: Checkout Sessions expire. */
export async function startPayment(): Promise<CheckoutSession | null> {
  return api.post<CheckoutSession>("/checkout/session");
}

export async function fetchConfirmation(orderId: string): Promise<Confirmation | null> {
  return api.get<Confirmation>(`/checkout/order/${encodeURIComponent(orderId)}`);
}
