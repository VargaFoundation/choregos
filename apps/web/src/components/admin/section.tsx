// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { SchemaForm, champsManquants, valeursParDefaut, type JsonSchema } from "@/components/schema-form";
import { Badge, Button, Card, Empty, ErrorNote, Input, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import type { AdminAction, AdminForm, AdminSecretOnce, AdminSection, AdminTable } from "@/lib/types";

/**
 * Le chemin d'un bloc, rempli : `{org}` par l'organisation courante, tout autre `{…}` par la clé
 * de la ligne. Le manifeste dit `{id}` ; la route du greffon peut nommer son paramètre autrement,
 * le cœur les a rapprochés au démarrage.
 */
export function remplir(chemin: string, org: string, cle?: string): string {
  return chemin
    .replaceAll("{org}", encodeURIComponent(org))
    .replace(/\{[^}]+\}/g, encodeURIComponent(cle ?? ""));
}

/** Une section déclarée par un greffon, rendue par les blocs de la console (ADR 0032). */
export function SectionRendue({ section, org }: { section: AdminSection; org: string }) {
  return (
    <div className="space-y-4" data-testid={`admin-section-${section.id}`}>
      {section.description && <p className="max-w-3xl text-sm text-ink-muted">{section.description}</p>}
      {section.blocks.map((bloc, index) => {
        if (bloc.kind === "form") return <BlocFormulaire key={index} bloc={bloc} org={org} />;
        if (bloc.kind === "table") return <BlocTable key={index} bloc={bloc} org={org} />;
        if (bloc.kind === "secret_once") return <BlocSecret key={index} bloc={bloc} org={org} />;
        return (
          <Card key={index} title={bloc.label}>
            <BoutonDAction action={bloc} org={org} />
          </Card>
        );
      })}
    </div>
  );
}

function BlocFormulaire({ bloc, org }: { bloc: AdminForm; org: string }) {
  const schema = bloc.schema as JsonSchema;
  const lu = useQuery({
    queryKey: ["admin-form", org, bloc.read],
    queryFn: () => api.sectionCall<Record<string, unknown>>("GET", remplir(bloc.read, org)),
  });
  const [saisie, setSaisie] = useState<Record<string, unknown> | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const valeurs = saisie ?? { ...valeursParDefaut(schema), ...(lu.data ?? {}) };
  const manquants = champsManquants(schema, valeurs);

  async function enregistrer() {
    setMessage(null);
    try {
      // Les seuls champs du schéma : la lecture peut rendre plus (un état complet), et une route
      // d'écriture stricte refuserait le reste.
      const champs = new Set(Object.keys(schema.properties ?? {}));
      const corps = Object.fromEntries(Object.entries(valeurs).filter(([nom]) => champs.has(nom)));
      await api.sectionCall(bloc.write_method ?? "PUT", remplir(bloc.write, org), corps);
      setMessage("saved");
      setSaisie(null);
      await lu.refetch();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "refused");
    }
  }

  return (
    <Card
      title={bloc.title}
      action={
        <Button size="sm" tone="primary" onClick={enregistrer} disabled={saisie === null || manquants.length > 0}>
          save
        </Button>
      }
    >
      {bloc.description && <p className="mb-3 text-sm text-ink-muted">{bloc.description}</p>}
      {lu.error ? (
        <ErrorNote>{String(lu.error)}</ErrorNote>
      ) : (
        <SchemaForm schema={schema} value={valeurs} onChange={setSaisie} disabled={lu.isLoading} />
      )}
      {message && (
        <p className="mt-2 text-sm" role="status">
          {message}
        </p>
      )}
    </Card>
  );
}

function BlocTable({ bloc, org }: { bloc: AdminTable; org: string }) {
  const lignes = useQuery({
    queryKey: ["admin-table", org, bloc.list],
    queryFn: async () => {
      const reponse = await api.sectionCall<unknown>("GET", remplir(bloc.list, org));
      return (Array.isArray(reponse) ? reponse : ((reponse as { items?: unknown[] })?.items ?? [])) as Array<
        Record<string, unknown>
      >;
    },
  });
  const cleDeLigne = bloc.row_key ?? "id";
  return (
    <Card title={bloc.title}>
      {bloc.description && <p className="mb-3 text-sm text-ink-muted">{bloc.description}</p>}
      {lignes.error && <ErrorNote>{String(lignes.error)}</ErrorNote>}
      {lignes.data && lignes.data.length === 0 && <Empty>nothing yet</Empty>}
      {lignes.data && lignes.data.length > 0 && (
        <Table aria-label={bloc.title}>
          <THead>
            <TR>
              {bloc.columns.map((colonne) => (
                <TH key={colonne.key}>{colonne.label}</TH>
              ))}
              {bloc.row_actions && bloc.row_actions.length > 0 && (
                <TH>
                  <span className="sr-only">actions</span>
                </TH>
              )}
            </TR>
          </THead>
          <TBody>
            {lignes.data.map((ligne, index) => (
              <TR key={String(ligne[cleDeLigne] ?? index)}>
                {bloc.columns.map((colonne) => (
                  <TD key={colonne.key}>
                    <Cellule valeur={ligne[colonne.key]} format={colonne.format} />
                  </TD>
                ))}
                {bloc.row_actions && bloc.row_actions.length > 0 && (
                  <TD>
                    <span className="inline-flex gap-2">
                      {bloc.row_actions.map((action) => (
                        <BoutonDAction
                          key={action.label}
                          action={action}
                          org={org}
                          cle={String(ligne[cleDeLigne] ?? "")}
                          onDone={() => void lignes.refetch()}
                        />
                      ))}
                    </span>
                  </TD>
                )}
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </Card>
  );
}

function Cellule({ valeur, format }: { valeur: unknown; format?: string }) {
  if (valeur === undefined || valeur === null || valeur === "") return <span className="text-ink-muted">—</span>;
  if (format === "date") return <>{shortDate(String(valeur))}</>;
  if (format === "badge") return <Badge tone="neutral">{String(valeur)}</Badge>;
  if (format === "code") return <code className="text-xs">{String(valeur)}</code>;
  return <>{typeof valeur === "object" ? JSON.stringify(valeur) : String(valeur)}</>;
}

/**
 * Une action : confirmée quand le manifeste le demande, ses paramètres demandés d'abord. Une
 * action `reauth` passe par une session fraîche — `lib/api.ts` suit `step_up_required`.
 */
function BoutonDAction({
  action,
  org,
  cle,
  onDone,
}: {
  action: AdminAction;
  org: string;
  cle?: string;
  onDone?: () => void;
}) {
  const client = useQueryClient();
  const params = action.params as JsonSchema | undefined;
  const [ouvert, setOuvert] = useState(false);
  const [valeurs, setValeurs] = useState<Record<string, unknown>>({});
  const [message, setMessage] = useState<string | null>(null);
  const aConfirmer = Boolean(action.confirm || params || action.danger);

  async function agir() {
    setMessage(null);
    try {
      await api.sectionCall(action.method ?? "POST", remplir(action.path, org, cle), params ? valeurs : undefined);
      setOuvert(false);
      setValeurs({});
      setMessage(`${action.label}: done`);
      onDone?.();
      await client.invalidateQueries({ queryKey: ["admin-table", org] });
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "refused");
    }
  }

  if (!ouvert) {
    return (
      <span className="inline-flex items-center gap-2">
        <Button size="sm" tone={action.danger ? "danger" : "default"} onClick={() => (aConfirmer ? setOuvert(true) : void agir())}>
          {action.label}
        </Button>
        {message && (
          <span className="text-xs" role="status">
            {message}
          </span>
        )}
      </span>
    );
  }
  return (
    <div className="space-y-2 rounded border border-line p-2" role="group" aria-label={`confirm ${action.label}`}>
      {action.confirm && <p className="text-sm">{action.confirm}</p>}
      {action.reauth && <p className="text-xs text-ink-muted">You may be asked to sign in again.</p>}
      {params && <SchemaForm schema={params} value={valeurs} onChange={setValeurs} />}
      <span className="inline-flex gap-2">
        <Button
          size="sm"
          tone={action.danger ? "danger" : "primary"}
          onClick={() => void agir()}
          disabled={params ? champsManquants(params, valeurs).length > 0 : false}
        >
          confirm {action.label}
        </Button>
        <Button size="sm" onClick={() => setOuvert(false)}>
          cancel
        </Button>
      </span>
      {message && (
        <p className="text-xs" role="status">
          {message}
        </p>
      )}
    </div>
  );
}

/** Un secret rendu une fois : montré ici, jamais gardé — ni en mémoire de requête, ni ailleurs. */
function BlocSecret({ bloc, org }: { bloc: AdminSecretOnce; org: string }) {
  const client = useQueryClient();
  const params = bloc.params as JsonSchema | undefined;
  const [valeurs, setValeurs] = useState<Record<string, unknown>>({});
  const [secret, setSecret] = useState<string | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);

  async function obtenir() {
    setErreur(null);
    try {
      const reponse = await api.sectionCall<Record<string, unknown>>(
        bloc.method ?? "POST",
        remplir(bloc.path, org),
        params ? valeurs : undefined,
      );
      setSecret(String(reponse?.[bloc.secret_field] ?? ""));
      setValeurs({});
      await client.invalidateQueries({ queryKey: ["admin-table", org] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
    }
  }

  return (
    <Card title={bloc.label}>
      {bloc.description && <p className="mb-3 text-sm text-ink-muted">{bloc.description}</p>}
      {secret ? (
        <div className="space-y-2" data-testid="secret-once">
          <p className="text-sm text-warn">Copy it now: it is shown once, and Choregos does not keep it.</p>
          <Input readOnly aria-label={`${bloc.label}: the secret`} value={secret} className="w-full font-mono" />
          <Button size="sm" onClick={() => setSecret(null)}>
            I have copied it
          </Button>
        </div>
      ) : (
        <div className="space-y-3">
          {params && <SchemaForm schema={params} value={valeurs} onChange={setValeurs} />}
          {bloc.confirm && <p className="text-sm text-ink-muted">{bloc.confirm}</p>}
          <Button tone="accent" onClick={() => void obtenir()} disabled={params ? champsManquants(params, valeurs).length > 0 : false}>
            {bloc.label}
          </Button>
          {erreur && <ErrorNote>{erreur}</ErrorNote>}
        </div>
      )}
    </Card>
  );
}
