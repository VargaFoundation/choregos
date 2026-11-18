import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';

// Where the site is published and where its source lives: the publishing identity (ADR 0156), read, not copied.
const identity = JSON.parse(
  readFileSync(resolve(__dirname, '../packages/core/src/choregos_core/publishing.json'), 'utf8'),
);
const documentation = new URL(identity.documentation);
const [organizationName, projectName] = new URL(identity.source_repository).pathname.slice(1).split('/');

// Its pages are `docs/site/` of this repository, copied to `website/docs/` by `scripts/import-docs.mjs`.
// The address is the publishing identity's `documentation` (packages/core/src/choregos_core/publishing.json).
const config: Config = {
  title: 'Tranibor',
  tagline: 'Governed agents: workflows carried by agents and people, under budgets and checks you can read.',
  favicon: 'img/favicon.png',

  future: {v4: true},

  url: documentation.origin,
  baseUrl: `${documentation.pathname.replace(/\/$/, '')}/`,

  organizationName,
  projectName,

  onBrokenLinks: 'throw',
  onBrokenMarkdownLinks: 'warn',

  // Mermaid diagrams in code blocks (```mermaid).
  markdown: {mermaid: true},
  themes: ['@docusaurus/theme-mermaid'],

  plugins: [
    [
      // llms.txt and llms-full.txt (https://llmstxt.org/) at the site's root.
      'docusaurus-plugin-llms',
      {
        title: 'Tranibor',
        description: 'Governed agents: workflows carried by agents and people, under budgets and checks you can read.',
        excludeImports: true,
        removeDuplicateHeadings: true,
      },
    ],
  ],

  i18n: {defaultLocale: 'en', locales: ['en']},

  presets: [
    [
      'classic',
      {
        docs: {
          routeBasePath: '/',
          sidebarPath: './sidebars.ts',
          // "Edit this page" opens the page's source, in the repository's `docs/site/`.
          editUrl: ({docPath}) => `${identity.source_repository}/edit/main/docs/site/${docPath}`,
        },
        blog: false,
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    navbar: {
      title: 'Tranibor',
      items: [
        {type: 'docSidebar', sidebarId: 'tutorialSidebar', position: 'left', label: 'Docs'},
        {href: identity.source_repository, label: 'GitHub', position: 'right'},
      ],
    },
    footer: {
      style: 'dark',
      copyright: `Copyright © ${new Date().getFullYear()} GRAAL.SYSTEMS SAS and the Tranibor contributors. Apache-2.0.`,
    },
    prism: {theme: prismThemes.github, darkTheme: prismThemes.dracula},
  } satisfies Preset.ThemeConfig,
};

export default config;
