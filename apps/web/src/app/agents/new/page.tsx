// SPDX-License-Identifier: Apache-2.0
"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { slugFromName } from "@/components/agents/builder";
import { useSession } from "@/lib/session";
import { api, ApiError } from "@/lib/api";
import type { AgentDraft } from "@/lib/types";
import { Alert, Breadcrumbs, Button, Card, Empty, ErrorNote, Heading, Input, Lead, Textarea, buttonClasses } from "@/components/ui";

// The builder is the heaviest form of the console: the first step does not need it (S32-01).
const AgentBuilder = dynamic(() => import("@/components/agents/builder").then((m) => m.AgentBuilder), {
  loading: () => <p className="text-sm text-ink-muted">Loading the builder…</p>,
});

const MIN_DESCRIPTION = 10;

/**
 * A new agent, on a full page (S39-05). Step 1 asks for two things only — a name and what the agent
 * should do. Step 2 is the builder, with its advanced sections folded, a "Save as draft" next to
 * "Create and activate", and the trial and the costs in a side panel. The step is in the address
 * (`?step=configure`), so the browser's Back goes to the first one.
 */
function NewAgent() {
  const router = useRouter();
  const search = useSearchParams();
  const step = search.get("step") === "configure" ? "configure" : "describe";
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  // The values that step 2 starts from, taken when one leaves step 1.
  const [initial, setInitial] = useState<{ display_name: string; slug: string; description: string } | null>(null);
  // What a draft adds to step 2: its spec, its Try it sample and what the platform changed in it.
  const [start, setStart] = useState<AgentDraft["spec"]>({});
  const [sample, setSample] = useState<AgentDraft["sample"] | undefined>(undefined);
  const [notes, setNotes] = useState<string[]>([]);
  const [drafting, setDrafting] = useState(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  const { org } = useSession();
  const ready = name.trim().length > 0 && description.trim().length >= MIN_DESCRIPTION;

  function toConfigure() {
    setInitial({ display_name: name.trim(), slug: slugFromName(name), description: description.trim() });
    setStart({});
    setSample(undefined);
    setNotes([]);
    router.push("/agents/new?step=configure");
  }

  /** One model call; nothing is saved. The form opens pre-filled and a person reviews it. */
  async function draftIt() {
    setDraftError(null);
    setDrafting(true);
    try {
      const draft = await api.draftAgent(org, { display_name: name.trim(), description: description.trim() });
      setInitial({ display_name: draft.display_name, slug: draft.slug, description: draft.description });
      setStart(draft.spec);
      setSample(draft.sample);
      setNotes(draft.notes ?? []);
      router.push("/agents/new?step=configure");
    } catch (cause) {
      setDraftError(
        cause instanceof ApiError && cause.status === 429
          ? "Too many drafts in a minute: wait a moment, or start from a blank form."
          : `The draft failed: ${cause instanceof Error ? cause.message : "unknown error"}. You can start from a blank form.`,
      );
    } finally {
      setDrafting(false);
    }
  }

  return (
    <div className="space-y-6">
      <Breadcrumbs steps={[{ href: "/agents", label: "Agents" }, { label: "New agent" }]} />
      <div className="space-y-2">
        <Heading as="h1" size="xl">
          New agent
        </Heading>
        <Lead>
          {step === "describe"
            ? "Say what the agent is for. Everything else has a default and can wait."
            : "Review the instructions, then create it. Sections you have not touched keep the project's defaults."}
        </Lead>
      </div>

      {step === "describe" || !initial ? (
        <Card title="Describe" className="max-w-3xl" data-testid="step-describe">
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (ready) toConfigure();
            }}
          >
            <label className="block space-y-1">
              <span className="text-xs text-ink-muted">Name</span>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Release notes writer" />
            </label>
            <div className="space-y-1">
              <label htmlFor="agent-description" className="block text-xs text-ink-muted">
                What should it do?
              </label>
              <Textarea
                id="agent-description"
                rows={5}
                maxLength={4000}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Writes the release notes of a project from its merged pull requests, in the tone of our changelog."
              />
              <p className="text-xs text-ink-muted">{MIN_DESCRIPTION} characters at least. It becomes the agent&apos;s description.</p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Button type="button" tone="primary" disabled={!ready || drafting || !org} onClick={() => void draftIt()}>
                {drafting ? "Drafting…" : "Draft it for me"}
              </Button>
              <Button type="submit" disabled={!ready || drafting}>
                Start from a blank form
              </Button>
              <Link href="/agents?catalogue=1" className={buttonClasses("ghost")}>
                Or add one from the catalogue
              </Link>
            </div>
            {drafting && (
              <p role="status" className="text-sm text-ink-muted">
                A model is drafting the instructions — nothing is saved.
              </p>
            )}
            {draftError && <ErrorNote>{draftError}</ErrorNote>}
          </form>
        </Card>
      ) : org ? (
        <>
          {notes.length > 0 && (
            <Alert tone="warn" title="The platform changed the draft">
              <ul className="list-disc pl-5">
                {notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </Alert>
          )}
          <AgentBuilder
            org={org}
            mode={{ kind: "create" }}
            start={start}
            initial={initial}
            initialSample={sample}
            onBack={() => router.back()}
            onCreated={(agent) => router.push(`/agents/${agent.slug}`)}
          />
        </>
      ) : (
        <Empty>Reading your session…</Empty>
      )}
    </div>
  );
}

export default function NewAgentPage() {
  return (
    <Suspense fallback={<Empty>Loading…</Empty>}>
      <NewAgent />
    </Suspense>
  );
}
