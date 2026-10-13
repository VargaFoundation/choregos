// SPDX-License-Identifier: Apache-2.0
/**
 * What a live build gets in place of the demo fixtures (`next.config.mjs`, `resolveAlias`).
 *
 * The fixtures are only ever loaded behind `IS_MOCK`, but the bundler emits the chunk of every
 * `import()` it sees, live build or not: 84 kB of fake data were shipped — and counted by the
 * bundle budget — with every live console. A live build resolves `@/mocks/data` to this file.
 */
export function mockApi<T>(): Promise<T> {
  return Promise.reject(new Error("the demo fixtures are not part of a live build"));
}

export const runEvents: never[] = [];
