// Déclarations des imports de fichiers non-TypeScript.
//
// TypeScript 6 refuse un import à effet de bord dont il ne trouve ni module ni déclaration
// (`TS2882`) — `import "./globals.css"` dans `layout.tsx`, `import "@xyflow/react/dist/style.css"`
// dans le graphe de workflow. Next les gère à la construction ; il faut juste le dire au
// compilateur. Additif : TypeScript 5 accepte ces déclarations sans rien changer.
declare module "*.css";
declare module "*.scss";
