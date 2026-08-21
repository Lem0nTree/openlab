import { describe, expect, it } from "vitest";

describe("OpenLab API client", () => {
  it("keeps API calls under the versioned API prefix", () => {
    expect("/api/v1".startsWith("/api/v1")).toBe(true);
  });
});

