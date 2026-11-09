import { describe, expect, it } from "vitest";
import { browserLabel, describeBrowser } from "@/lib/user-agent";

/** A session's browser, in words (ADR 0130). */
describe("describeBrowser", () => {
  it.each([
    [
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
      "Chrome on Windows",
      "computer",
    ],
    [
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0 Safari/537.36 Edg/142.0",
      "Edge on Windows",
      "computer",
    ],
    ["Mozilla/5.0 (Macintosh; Intel Mac OS X 15_1) Gecko/20100101 Firefox/144.0", "Firefox on macOS", "computer"],
    [
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
      "Safari on macOS",
      "computer",
    ],
    [
      "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",
      "Safari on iOS",
      "phone",
    ],
    [
      "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0 Mobile Safari/537.36",
      "Chrome on Android",
      "phone",
    ],
    [
      "Mozilla/5.0 (Linux; Android 15; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0 Safari/537.36",
      "Chrome on Android",
      "tablet",
    ],
    ["Mozilla/5.0 (X11; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0", "Firefox on Linux", "computer"],
  ])("%s", (ua, label, device) => {
    expect(browserLabel(ua)).toBe(label);
    expect(describeBrowser(ua).device).toBe(device);
  });

  it("says it does not know rather than guessing", () => {
    expect(browserLabel(null)).toBe("Unknown browser on an unknown system");
  });
});
