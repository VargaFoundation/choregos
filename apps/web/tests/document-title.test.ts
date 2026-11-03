import { describe, expect, it } from "vitest";
import { titleFromPath } from "@/app/document-title";

describe("a page's title (S23-05)", () => {
  it("goes from the most specific to the broadest, and each page has its own", () => {
    expect(titleFromPath("/")).toBe("projects · choregos");
    expect(titleFromPath("/inbox")).toBe("inbox · choregos");
    expect(titleFromPath("/p/billing-api")).toBe("billing-api · choregos");
    expect(titleFromPath("/p/billing-api/board")).toBe("board · billing-api · choregos");
    expect(titleFromPath("/p/billing-api/items/w1")).toBe("ticket w1 · billing-api · choregos");
    expect(titleFromPath("/p/billing-api/runs/r3")).toBe("run r3 · billing-api · choregos");
    expect(titleFromPath("/p/billing-api/workflows/default-simple/map")).toBe(
      "map · default-simple · workflows · billing-api · choregos",
    );
    expect(titleFromPath("/admin/x/scim")).toBe("scim · section · admin · choregos");
  });
});

describe("the title suffix follows the product name (S39-03b)", () => {
  it("keeps the lower-case default and uses another product name as it is", () => {
    expect(titleFromPath("/inbox", "Choregos")).toBe("inbox · choregos");
    expect(titleFromPath("/inbox", "Sample")).toBe("inbox · Sample");
    expect(titleFromPath("/", "Sample")).toBe("projects · Sample");
  });
});
