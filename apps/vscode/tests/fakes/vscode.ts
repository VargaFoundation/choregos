// SPDX-License-Identifier: Apache-2.0
/**
 * A scriptable fake of the editor's API, the part the extension uses (ADR 0005: every integration has
 * its fake). Vitest resolves `vscode` to this module; `fake` holds what the extension registered and lets
 * a test play the person's answers.
 */
type Listener<T> = (value: T) => unknown;

export class Disposable {
  constructor(private readonly onDispose: () => void = () => undefined) {}
  dispose(): void {
    this.onDispose();
  }
}

export class EventEmitter<T> {
  private listeners: Listener<T>[] = [];
  event = (listener: Listener<T>): Disposable => {
    this.listeners.push(listener);
    return new Disposable(() => {
      this.listeners = this.listeners.filter((l) => l !== listener);
    });
  };
  fire(value: T): void {
    for (const listener of [...this.listeners]) listener(value);
  }
  dispose(): void {
    this.listeners = [];
  }
}

export class ThemeColor {
  constructor(readonly id: string) {}
}

export class ThemeIcon {
  constructor(
    readonly id: string,
    readonly color?: ThemeColor,
  ) {}
}

export enum TreeItemCollapsibleState {
  None = 0,
  Collapsed = 1,
  Expanded = 2,
}

export class TreeItem {
  id?: string;
  description?: string;
  tooltip?: string;
  iconPath?: ThemeIcon;
  contextValue?: string;
  accessibilityInformation?: { label: string; role?: string };
  constructor(
    public label: string,
    public collapsibleState: TreeItemCollapsibleState = TreeItemCollapsibleState.None,
  ) {}
}

export enum StatusBarAlignment {
  Left = 1,
  Right = 2,
}

export class FakeOutputChannel {
  lines: string[] = [];
  shown = false;
  info(line: string): void {
    this.lines.push(line);
  }
  warn(line: string): void {
    this.lines.push(line);
  }
  error(line: string): void {
    this.lines.push(line);
  }
  show(): void {
    this.shown = true;
  }
  dispose(): void {
    /* nothing */
  }
}

export interface FakeTreeView {
  id: string;
  provider: unknown;
  badge?: { value: number; tooltip: string } | undefined;
  message?: string | undefined;
  description?: string | undefined;
  dispose(): void;
}

export class CancellationError extends Error {
  constructor() {
    super("Canceled");
    this.name = "Canceled";
  }
}

interface AuthenticationProvider {
  getSessions(): Promise<unknown[]>;
  createSession(scopes: string[]): Promise<unknown>;
  removeSession(id: string): Promise<void>;
}

export const fake = {
  commands: new Map<string, (...args: unknown[]) => unknown>(),
  executed: [] as { id: string; args: unknown[] }[],
  treeViews: new Map<string, FakeTreeView>(),
  outputs: new Map<string, FakeOutputChannel>(),
  settings: new Map<string, unknown>(),
  authProviders: new Map<string, { label: string; provider: AuthenticationProvider }>(),
  /** What the person types in the next input boxes, in order; `undefined` is Escape. */
  inputs: [] as (string | undefined)[],
  inputBoxes: [] as Record<string, unknown>[],
  messages: [] as { level: "error" | "info" | "warning"; text: string }[],
  configurationChanged: new EventEmitter<{ affectsConfiguration(section: string): boolean }>(),
  reset(): void {
    this.commands.clear();
    this.executed = [];
    this.treeViews.clear();
    this.outputs.clear();
    this.settings.clear();
    this.authProviders.clear();
    this.inputs = [];
    this.inputBoxes = [];
    this.messages = [];
    this.configurationChanged = new EventEmitter();
  },
  /** The person changes a setting. */
  set(key: string, value: unknown): void {
    this.settings.set(key, value);
    this.configurationChanged.fire({ affectsConfiguration: (section) => key.startsWith(section) });
  },
};

export const window = {
  showInputBox(options: Record<string, unknown>): Promise<string | undefined> {
    fake.inputBoxes.push(options);
    return Promise.resolve(fake.inputs.shift());
  },
  showErrorMessage(text: string): Promise<undefined> {
    fake.messages.push({ level: "error", text });
    return Promise.resolve(undefined);
  },
  showInformationMessage(text: string): Promise<undefined> {
    fake.messages.push({ level: "info", text });
    return Promise.resolve(undefined);
  },
  showWarningMessage(text: string): Promise<undefined> {
    fake.messages.push({ level: "warning", text });
    return Promise.resolve(undefined);
  },
  createOutputChannel(name: string): FakeOutputChannel {
    const channel = new FakeOutputChannel();
    fake.outputs.set(name, channel);
    return channel;
  },
  createTreeView(id: string, options: { treeDataProvider: unknown }): FakeTreeView {
    const view: FakeTreeView = {
      id,
      provider: options.treeDataProvider,
      dispose: () => fake.treeViews.delete(id),
    };
    fake.treeViews.set(id, view);
    return view;
  },
};

export const commands = {
  registerCommand(id: string, handler: (...args: unknown[]) => unknown): Disposable {
    if (fake.commands.has(id)) throw new Error(`command ${id} registered twice`);
    fake.commands.set(id, handler);
    return new Disposable(() => fake.commands.delete(id));
  },
  executeCommand(id: string, ...args: unknown[]): Promise<unknown> {
    fake.executed.push({ id, args });
    const handler = fake.commands.get(id);
    return Promise.resolve(handler?.(...args));
  },
};

export const authentication = {
  registerAuthenticationProvider(id: string, label: string, provider: AuthenticationProvider): Disposable {
    fake.authProviders.set(id, { label, provider });
    return new Disposable(() => fake.authProviders.delete(id));
  },
  async getSession(id: string, scopes: string[], options: { createIfNone?: boolean } = {}): Promise<unknown> {
    const registered = fake.authProviders.get(id);
    if (!registered) throw new Error(`no authentication provider ${id}`);
    const [existing] = await registered.provider.getSessions();
    if (existing || !options.createIfNone) return existing;
    return registered.provider.createSession(scopes);
  },
};

export const workspace = {
  onDidChangeConfiguration(
    listener: (event: { affectsConfiguration(section: string): boolean }) => unknown,
  ): Disposable {
    return fake.configurationChanged.event(listener);
  },
  getConfiguration(section: string) {
    return {
      get<T>(key: string, fallback?: T): T | undefined {
        const value = fake.settings.get(`${section}.${key}`);
        return value === undefined ? fallback : (value as T);
      },
    };
  },
};

/** A minimal `ExtensionContext`: subscriptions, the manifest, and secret storage in memory. */
export function extensionContext(version = "0.0.0-test") {
  const secrets = new Map<string, string>();
  return {
    subscriptions: [] as { dispose(): unknown }[],
    extension: { packageJSON: { version } },
    secrets: {
      get: (key: string) => Promise.resolve(secrets.get(key)),
      store: (key: string, value: string) => {
        secrets.set(key, value);
        return Promise.resolve();
      },
      delete: (key: string) => {
        secrets.delete(key);
        return Promise.resolve();
      },
      onDidChange: new EventEmitter<{ key: string }>().event,
    },
    stored: secrets,
  };
}
