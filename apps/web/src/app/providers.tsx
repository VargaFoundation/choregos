// SPDX-License-Identifier: Apache-2.0
"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { EgressProvider } from "@/components/egress/egress-provider";
import { SessionProvider } from "@/lib/session";
import { ThemeProvider } from "@/lib/theme";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 5_000, refetchOnWindowFocus: false, retry: 1 },
        },
      }),
  );
  return (
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <SessionProvider>
          <EgressProvider>{children}</EgressProvider>
        </SessionProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
