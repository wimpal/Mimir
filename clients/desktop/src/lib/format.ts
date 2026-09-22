/** Strip trailing slash and accidental `/v1` API suffix. */
export function normalizeBrainUrl(url: string): string {
  const text = (url || "").trim();
  if (!text) throw new Error("brain URL is empty");
  let u: URL;
  try {
    u = new URL(text);
  } catch {
    throw new Error(`invalid brain URL: ${url}`);
  }
  let path = (u.pathname || "").replace(/\/+$/, "");
  if (path === "/v1" || path.endsWith("/v1")) {
    path = path === "/v1" ? "" : path.slice(0, -3).replace(/\/+$/, "");
  }
  u.pathname = path || "/";
  u.search = "";
  u.hash = "";
  let out = u.toString();
  if (out.endsWith("/") && u.pathname === "/") {
    out = out.slice(0, -1);
  } else if (out.endsWith("/") && path) {
    out = out.replace(/\/+$/, "");
  }
  // URL.toString keeps trailing slash for root path sometimes
  if (out.endsWith("/") && !path) {
    out = out.slice(0, -1);
  }
  return out.replace(/\/$/, "");
}

/** True when the brain URL host is loopback-only. */
export function isLoopbackBrainUrl(url: string): boolean {
  try {
    const host = new URL(normalizeBrainUrl(url)).hostname.toLowerCase();
    return host === "127.0.0.1" || host === "localhost" || host === "::1";
  } catch {
    return false;
  }
}
