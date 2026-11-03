// SPDX-License-Identifier: Apache-2.0
// Declarations of the non-TypeScript file imports.
//
// TypeScript 6 refuses a side-effect import for which it finds neither a module nor a declaration
// (`TS2882`): `import "./globals.css"` in `layout.tsx`. Next handles them at build time; the
// compiler only has to be told. Additive: TypeScript 5 accepts these declarations without change.
declare module "*.css";
declare module "*.scss";
