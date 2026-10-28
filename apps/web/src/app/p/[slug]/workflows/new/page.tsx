// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { use, useState } from "react";
import { Button, Card, ErrorNote, Field, Input, Select } from "@/components/ui";
import { renommer } from "@/components/workflows/renommer";
import { ApiError, api } from "@/lib/api";

const NOM = /^[a-z][a-z0-9-]{0,62}$/;

/**
 * Un workflow de plus dans le projet, à partir d'un gabarit : il naît actif, à côté des autres, et
 * ne reçoit une demande que si elle le nomme ou si une règle de routage l'y envoie.
 *
 * Le PUT par nom publie la version suivante d'un workflow qui existe déjà : nommer « onboarding » un
 * nouveau workflow remplaçait l'onboarding actif par le gabarit, sans un mot (seconde passe du 08/10,
 * S23-06). Le nom est vérifié pendant la frappe ; l'API, elle, refuse de créer un nom pris (`create_only`,
 * #336) — deux créations à la même seconde ne s'écrasent plus.
 */
export default function NewWorkflowPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  const router = useRouter();
  const client = useQueryClient();
  const templates = useQuery({
    queryKey: ["workflow-templates"],
    queryFn: () => api.workflowTemplates(),
  });
  // `?template=default-request&default=1`: the readiness fix of a project whose workflow asks for a
  // repository it does not have (ADR 0110 §4) — one gesture publishes the other workflow and makes it the default.
  const search = useSearchParams();
  const asked = search.get("template") ?? "";
  const [nom, setNom] = useState(asked);
  const [gabarit, setGabarit] = useState(asked);
  const [makeDefault, setMakeDefault] = useState(search.get("default") === "1");
  const [erreur, setErreur] = useState<string | null>(null);
  const [envoi, setEnvoi] = useState(false);
  const choisi = templates.data?.find(
    (t) => t.name === (gabarit || templates.data?.[0]?.name),
  );
  const existants = useQuery({
    queryKey: ["workflows", slug],
    queryFn: () => api.workflows(slug),
  });
  const nomValide = NOM.test(nom);
  const pris = existants.data?.some((w) => w.name === nom) ?? false;

  async function publier() {
    if (!choisi || !nomValide || pris) return;
    setEnvoi(true);
    setErreur(null);
    try {
      await api.createWorkflow(slug, nom, renommer(choisi.yaml, nom));
      if (makeDefault) {
        const routing = await api.workflowRouting(slug);
        await api.putWorkflowRouting(slug, { ...routing, default: nom });
        await client.invalidateQueries({ queryKey: ["readiness", slug] });
      }
      await client.invalidateQueries({ queryKey: ["workflows", slug] });
      // Born on the map (ADR 0109 §5): the YAML stays one tab away, never the only way.
      router.push(`/p/${slug}/workflows/${encodeURIComponent(nom)}/map?vue=list`);
    } catch (cause) {
      // Créé ailleurs entre-temps : la liste relue le dit, sous le nom, avec le lien vers lui.
      if (cause instanceof ApiError && cause.status === 409) await existants.refetch();
      else setErreur(cause instanceof Error ? cause.message : "publication refused");
    } finally {
      setEnvoi(false);
    }
  }

  return (
    <Card title="New workflow">
      <div className="max-w-xl space-y-4">
        <Field
          label="Name"
          hint={
            nom && (!nomValide || pris)
              ? undefined
              : "Lowercase letters, digits and dashes — e.g. offboarding"
          }
          error={
            nom && !nomValide
              ? "Lowercase letters, digits and dashes only"
              : pris
                ? `A workflow named ${nom} already exists — publishing would replace its active version`
                : undefined
          }
        >
          {(props) => (
            <Input
              {...props}
              value={nom}
              onChange={(event) => setNom(event.target.value)}
            />
          )}
        </Field>
        <Field label="Start from">
          {(props) => (
            <Select
              {...props}
              value={gabarit || choisi?.name || ""}
              onChange={(event) => setGabarit(event.target.value)}
            >
              {templates.data?.map((t) => (
                <option key={t.name} value={t.name}>
                  {t.name}
                  {t.description ? ` — ${t.description}` : ""}
                </option>
              ))}
            </Select>
          )}
        </Field>
        {pris && (
          <p className="text-sm">
            <Link
              href={`/p/${slug}/workflows/${encodeURIComponent(nom)}`}
            >
              Open {nom}
            </Link>{" "}
            to change it, or choose another name.
          </p>
        )}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={makeDefault} onChange={(event) => setMakeDefault(event.target.checked)} />
          Make it the project&apos;s default workflow
        </label>
        {erreur && <ErrorNote>{erreur}</ErrorNote>}
        <Button
          tone="accent"
          onClick={publier}
          disabled={!nomValide || !choisi || envoi || pris || !existants.data}
        >
          Publish {nom || "the workflow"}
        </Button>
      </div>
    </Card>
  );
}
