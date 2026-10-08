import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";

describe("une erreur de l'API sans explication", () => {
  it("se dit en anglais, avec son statut HTTP", () => {
    expect(new ApiError(502, {}).message).toBe("request failed (HTTP 502)");
  });

  it("garde le détail ou le titre que le serveur donne", () => {
    expect(new ApiError(422, { title: "Unprocessable", detail: "metadata.name differs" }).message).toBe("metadata.name differs");
    expect(new ApiError(404, { title: "Not found" }).message).toBe("Not found");
  });
});
