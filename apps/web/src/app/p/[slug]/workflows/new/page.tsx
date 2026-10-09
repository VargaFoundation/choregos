// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useState } from "react";
import { Field, Input, Select } from "@varga/design-system";
import { Button, Card, ErrorNote } from "@/components/ui";
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
  const [nom, setNom] = useState("");
  const [gabarit, setGabarit] = useState("");
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
      await client.invalidateQueries({ queryKey: ["workflows", slug] });
      router.push(`/p/${slug}/workflows/${encodeURIComponent(nom)}/yaml`);
    } catch (cause) {
      // Créé ailleurs entre-temps : la liste relue le dit, sous le nom, avec le lien vers lui.
      if (cause instanceof ApiError && cause.status === 409) await existants.refetch();
      else setErreur(cause instanceof Error ? cause.message : "publication refused");
    } finally {
      setEnvoi(false);
    }
  }

  return (
    <Card title="new workflow">
      <div className="max-w-xl space-y-4">
        <Field
          label="name"
          hint={
            nom && (!nomValide || pris)
              ? undefined
              : "lowercase letters, digits and dashes — e.g. offboarding"
          }
          error={
            nom && !nomValide
              ? "lowercase letters, digits and dashes only"
              : pris
                ? `a workflow named ${nom} already exists — publishing would replace its active version`
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
        <Field label="start from">
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
              open {nom}
            </Link>{" "}
            to change it, or choose another name.
          </p>
        )}
        {erreur && <ErrorNote>{erreur}</ErrorNote>}
        <Button
          tone="accent"
          onClick={publier}
          disabled={!nomValide || !choisi || envoi || pris || !existants.data}
        >
          publish {nom || "the workflow"}
        </Button>
      </div>
    </Card>
  );
}
