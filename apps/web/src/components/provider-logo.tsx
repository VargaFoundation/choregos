// SPDX-License-Identifier: Apache-2.0
import { cn } from "@/lib/cn";

/**
 * The mark of a model provider, as a monogram on a neutral tile. No brand artwork is drawn or fetched:
 * the console's CSP allows no outside image, and a provider's logo is its trademark. A monogram names the
 * provider without claiming to be it; the provider's name is the accessible label and sits beside it.
 */

export interface ProviderMark {
  /** The name shown beside the mark. */
  label: string;
  /** One or two letters. */
  monogram: string;
}

const KNOWN: Array<{ match: RegExp; mark: ProviderMark }> = [
  { match: /^openai$/, mark: { label: "OpenAI", monogram: "O" } },
  { match: /^azure(_ai|_text)?$|^azure[-/]/, mark: { label: "Azure OpenAI", monogram: "Az" } },
  { match: /^anthropic$/, mark: { label: "Anthropic", monogram: "A" } },
  { match: /^mistral(ai)?$|^codestral$/, mark: { label: "Mistral AI", monogram: "M" } },
  { match: /^(google|gemini|vertex_ai|vertex_ai-[a-z-]+|vertex)$/, mark: { label: "Google", monogram: "G" } },
  { match: /^(bedrock|sagemaker)$/, mark: { label: "AWS Bedrock", monogram: "Bd" } },
  { match: /^ollama(_chat)?$/, mark: { label: "Ollama", monogram: "Ol" } },
  { match: /^(local|vllm|hosted_vllm)$/, mark: { label: "Local", monogram: "Lo" } },
];

/** The mark for a provider string the gateway gave; an unknown provider keeps its own name and first letter. */
export function providerMark(provider: string | null | undefined): ProviderMark | null {
  const name = provider?.trim();
  if (!name) return null;
  const key = name.toLowerCase();
  const known = KNOWN.find((k) => k.match.test(key));
  return known?.mark ?? { label: name, monogram: name.charAt(0).toUpperCase() };
}

export function ProviderLogo({ provider, className }: { provider: string | null | undefined; className?: string }) {
  const mark = providerMark(provider);
  if (!mark) return null;
  return (
    <span
      role="img"
      aria-label={mark.label}
      title={mark.label}
      data-provider={mark.label}
      className={cn(
        "inline-flex size-6 shrink-0 select-none items-center justify-center rounded-md border border-line bg-surface-muted font-mono text-[11px] font-semibold leading-none text-ink",
        className,
      )}
    >
      {mark.monogram}
    </span>
  );
}
