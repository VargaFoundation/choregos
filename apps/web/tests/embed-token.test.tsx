import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SignedIn from "@/app/embed/signed-in/page";
import { clearEmbedToken, EMBED_HEADER, embedHeaders, isFrameSession, setEmbedToken, SIGNED_IN_MESSAGE } from "@/lib/embed-token";

const mocks = vi.hoisted(() => ({ mint: vi.fn(), params: new URLSearchParams() }));
vi.mock("next/navigation", () => ({ useSearchParams: () => mocks.params }));
vi.mock("@/lib/api", () => ({ api: { mintEmbedToken: mocks.mint } }));

beforeEach(() => {
  window.sessionStorage.clear();
  clearEmbedToken();
  mocks.mint.mockReset();
  mocks.params = new URLSearchParams("org=varga&installation=coach");
});
afterEach(() => vi.unstubAllGlobals());

describe("the frame token of the embedded component (S63-12)", () => {
  it("is sent while it lives and forgotten after", () => {
    expect(embedHeaders()).toEqual({});
    expect(isFrameSession()).toBe(false);
    setEmbedToken("tok", 900, 1_000);
    expect(embedHeaders(2_000)).toEqual({ [EMBED_HEADER]: "tok" });
    expect(isFrameSession()).toBe(true);
    expect(embedHeaders(1_000 + 900_000)).toEqual({});
    expect(window.sessionStorage.getItem("choregos.embed.token")).toBeNull();
  });

  it("is read back from the frame's sessionStorage after a reload", () => {
    window.sessionStorage.setItem("choregos.embed.token", JSON.stringify({ token: "kept", expiresAt: Date.now() + 60_000 }));
    expect(embedHeaders()).toEqual({ [EMBED_HEADER]: "kept" });
  });
});

describe("the page the sign-in popup lands on", () => {
  it("mints the token for the installation and hands it to its opener, on its own origin only", async () => {
    mocks.mint.mockResolvedValue({ token: "tok-9", expires_in: 900 });
    const opener = { postMessage: vi.fn() };
    Object.defineProperty(window, "opener", { value: opener, configurable: true });
    vi.spyOn(window, "close").mockImplementation(() => undefined);
    render(<SignedIn />);
    await waitFor(() =>
      expect(opener.postMessage).toHaveBeenCalledWith(
        { type: SIGNED_IN_MESSAGE, token: "tok-9", expires_in: 900 },
        window.location.origin,
      ),
    );
    expect(mocks.mint).toHaveBeenCalledWith("varga", "coach");
  });

  it("says so when the token is refused, and does not close", async () => {
    mocks.mint.mockRejectedValue(new Error("not in the audience"));
    const close = vi.spyOn(window, "close").mockImplementation(() => undefined);
    render(<SignedIn />);
    expect(await screen.findByText("not in the audience")).toBeInTheDocument();
    expect(close).not.toHaveBeenCalled();
  });
});
