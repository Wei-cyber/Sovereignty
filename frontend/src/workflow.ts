import type { Definition } from "./types";

export function graphProblem(definition: Definition): string | null {
  const nodes = definition.nodes;
  const ids = new Set(nodes.map((n) => n.id));
  if (ids.size !== nodes.length) return "Each step needs a unique ID.";
  const inputs = nodes.filter((n) => n.type === "input");
  if (inputs.length !== 1) return "Use exactly one input step.";
  if (!nodes.some((n) => n.type === "answer")) return "Add an answer step.";
  for (const edge of definition.edges)
    if (!ids.has(edge.source) || !ids.has(edge.target))
      return "Every connection must link existing steps.";
  for (const node of nodes) {
    const edges = definition.edges.filter((e) => e.source === node.id);
    if (node.type === "answer" && edges.length)
      return "Answer steps must be the end of a path.";
    if (
      node.type === "input" &&
      definition.edges.some((e) => e.target === node.id)
    )
      return "Input steps cannot receive connections.";
    if (
      node.type === "condition" &&
      (edges.length !== 2 ||
        new Set(edges.map((e) => e.branch)).size !== 2 ||
        !edges.every((e) => e.branch === "true" || e.branch === "false"))
    )
      return "A condition needs one true and one false connection.";
    if (
      !["answer", "condition"].includes(node.type) &&
      (edges.length !== 1 || edges[0].branch)
    )
      return "Connect every step to its next step. Use a condition to branch.";
  }
  const visiting = new Set<string>(),
    visited = new Set<string>();
  function visit(id: string): boolean {
    if (visiting.has(id)) return false;
    if (visited.has(id)) return true;
    visiting.add(id);
    for (const edge of definition.edges.filter((e) => e.source === id))
      if (!visit(edge.target)) return false;
    visiting.delete(id);
    visited.add(id);
    return true;
  }
  if (!visit(inputs[0].id))
    return "Cycles are not supported. Agent search loops are managed inside the agent step.";
  if (visited.size !== nodes.length)
    return "Connect every step to the input path.";
  return null;
}
