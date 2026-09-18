import { describe, expect, it } from "vitest";
import { graphProblem } from "./workflow";
import type { Definition } from "./types";
const valid: Definition = {
  schema_version: 1,
  nodes: [
    { id: "input", type: "input", label: "Question" },
    { id: "answer", type: "answer", label: "Answer" },
  ],
  edges: [{ id: "e1", source: "input", target: "answer", branch: null }],
};
describe("workflow validation", () => {
  it("accepts a connected sequence", () =>
    expect(graphProblem(valid)).toBeNull());
  it("rejects connections to unknown steps", () =>
    expect(
      graphProblem({
        ...valid,
        edges: [{ id: "bad", source: "input", target: "missing" }],
      }),
    ).toContain("existing"));
  it("rejects unsupported fanout", () =>
    expect(
      graphProblem({
        ...valid,
        edges: [
          ...valid.edges,
          { id: "another", source: "input", target: "answer" },
        ],
      }),
    ).toContain("condition"));
  it("rejects a disconnected step", () =>
    expect(
      graphProblem({
        ...valid,
        nodes: [
          ...valid.nodes,
          { id: "orphan", type: "answer", label: "Unused" },
        ],
      }),
    ).toContain("every step"));
});
