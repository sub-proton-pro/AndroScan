/// <reference types="vite/client" />

// Cytoscape extensions ship without their own .d.ts; we register them
// via ``cytoscape.use(ext)`` and never call them directly. Untyped
// default exports keep TypeScript quiet without forcing us to hand-roll
// the (large) layout option types.
declare module "cytoscape-dagre" {
  const ext: cytoscape.Ext;
  export default ext;
}
declare module "cytoscape-cose-bilkent" {
  const ext: cytoscape.Ext;
  export default ext;
}
declare module "cytoscape-popper" {
  const ext: cytoscape.Ext;
  export default ext;
}
