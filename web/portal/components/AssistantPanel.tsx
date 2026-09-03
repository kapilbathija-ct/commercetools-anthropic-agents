// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { AssistantPanel as PanelShell, type MerchantChat, type Prefill } from "web-shared";
import type { StagedChange } from "@/lib/types";
import GenerativeBlock from "./generative";

// No campaigns: commercetools has no campaign object, so enable_campaigns is off and
// the campaign tools are not registered at all. Offering it here would advertise something
// the assistant will then have to refuse.
const COPY = {
  title: "Merchant assistant",
  intro: "Ask about performance, inventory, pricing, or listing content.",
  starters: [
    "What needs my attention this morning?",
    "How did sales do this week compared to last?",
    "Which listings are running low on stock?",
    "Which slow movers should we mark down?",
  ],
  label: "Message the merchant assistant",
  placeholder: "Ask about sales, stock, pricing…",
};

export default function AssistantPanel({
  chat,
  prefill,
  onPrefill,
  ...shell
}: {
  chat: MerchantChat<StagedChange>;
  prefill: Prefill | null;
  onPrefill: (text: string) => void;
  newMemoryCount: number;
  onOpenActivity: () => void;
  onClose: () => void;
  fullscreen: boolean;
  onToggleFullscreen: () => void;
}) {
  return (
    <PanelShell
      chat={chat}
      copy={COPY}
      prefill={prefill}
      renderBlock={(segment) => (
        <GenerativeBlock
          block={segment.block}
          status={segment.status}
          onChangeAction={chat.actOnChange}
          onPrefill={onPrefill}
        />
      )}
      {...shell}
    />
  );
}
