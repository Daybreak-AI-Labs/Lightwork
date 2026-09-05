/* Shared by the connection form, atomic storage write, and request guard. */
export function connectionValidationError(baseUrl: string, token = ""): string | null {
  let url: URL;
  try {
    if (baseUrl.length > 4096 || /[\r\n]/.test(baseUrl)) throw new Error();
    url = new URL(baseUrl.trim());
  } catch {
    return "Enter your organization's full dashboard HTTPS address.";
  }
  if (!url.hostname || url.username || url.password || url.search || url.hash) {
    return "Use the dashboard address without sign-in details, a query, or a fragment.";
  }
  const host = url.hostname.toLowerCase();
  const loopback = ["localhost", "127.0.0.1", "::1", "[::1]"].includes(host);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) {
    return "Use an HTTPS dashboard address. HTTP is allowed only on this device.";
  }
  if (token.length > 8192 || /[\r\n]/.test(token.trim())) {
    return "The access token is too long or contains a line break. Check it and try again.";
  }
  return null;
}
