import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AvatarEditor } from "@/components/account/avatar-editor";
import { api, ApiError } from "@/lib/api";

/** My photo, on My account (S66-42, ADR 0162): uploaded by the person, refused before sending when it cannot pass. */
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: { ...real.api, putMyAvatar: vi.fn(), deleteMyAvatar: vi.fn() },
  };
});

function wrap(url: string | null = null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <AvatarEditor name="Vincent Devillers" url={url} />
    </QueryClientProvider>,
  );
  return invalidate;
}

const choose = (file: File) => fireEvent.change(screen.getByTestId("avatar-input"), { target: { files: [file] } });
const file = (type: string, bytes: number) => new File([new Uint8Array(bytes)], "me", { type });

describe("my photo", () => {
  beforeEach(() => {
    vi.mocked(api.putMyAvatar).mockReset().mockResolvedValue({ avatar_url: "/api/v1/users/u1/avatar?v=1" });
    vi.mocked(api.deleteMyAvatar).mockReset().mockResolvedValue(undefined);
  });

  it("shows the initials and offers an upload without a photo", () => {
    wrap();
    expect(screen.getByTestId("avatar-initials").textContent).toBe("VD");
    expect(screen.getByRole("button", { name: "Upload a photo" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Remove photo" })).toBeNull();
    expect(screen.getByTestId("avatar-input").getAttribute("accept")).toBe("image/png,image/jpeg,image/webp");
  });

  it("sends a PNG under 2 MiB, then reads the session again", async () => {
    const invalidate = wrap();
    const png = file("image/png", 1024);
    choose(png);
    await waitFor(() => expect(api.putMyAvatar).toHaveBeenCalledWith(png));
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["me"] }));
  });

  it("refuses a GIF without calling the API", () => {
    wrap();
    choose(file("image/gif", 1024));
    expect(screen.getByText("A photo is a PNG, JPEG or WebP image.")).toBeTruthy();
    expect(api.putMyAvatar).not.toHaveBeenCalled();
  });

  it("refuses a file over 2 MiB without calling the API", () => {
    wrap();
    choose(file("image/jpeg", 2 * 1024 * 1024 + 1));
    expect(screen.getByText("A photo is at most 2 MiB.")).toBeTruthy();
    expect(api.putMyAvatar).not.toHaveBeenCalled();
  });

  it("says what the API refused", async () => {
    vi.mocked(api.putMyAvatar).mockRejectedValue(
      new ApiError(422, { title: "Unprocessable", detail: "the image is 8000 pixels wide: at most 4096" }),
    );
    wrap();
    choose(file("image/webp", 1024));
    expect(await screen.findByText("the image is 8000 pixels wide: at most 4096")).toBeTruthy();
  });

  it("removes the photo", async () => {
    wrap("/api/v1/users/u1/avatar?v=1");
    expect(screen.getByTestId("avatar-photo").getAttribute("src")).toBe("/api/v1/users/u1/avatar?v=1");
    fireEvent.click(screen.getByRole("button", { name: "Remove photo" }));
    await waitFor(() => expect(api.deleteMyAvatar).toHaveBeenCalledOnce());
  });
});

describe("the request", () => {
  it("sends the file as multipart, in the field `file`", async () => {
    const real = (await vi.importActual<typeof import("@/lib/api")>("@/lib/api")).api;
    const fetched = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ avatar_url: "/api/v1/users/u1/avatar?v=1" }), { status: 200 }),
    );
    const png = file("image/png", 10);
    await real.putMyAvatar(png);
    const [url, init] = fetched.mock.calls[0]!;
    expect(url).toBe("/api/v1/me/avatar");
    expect(init?.method).toBe("PUT");
    expect((init?.body as FormData).get("file")).toBeInstanceOf(File);
    fetched.mockRestore();
  });
});
