/** Only allow a local application page as the post-login destination. */
export function loginDestination(value?: string | null): string {
  if (!value?.startsWith("/") || value.startsWith("//")) return "/";

  try {
    const decoded = decodeURIComponent(value);
    if (decoded.startsWith("//") || decoded.includes("\\") ||
        [...decoded].some((character) => character.charCodeAt(0) <= 32)) return "/";

    const url = new URL(value, "http://openlab.invalid");
    const pathname = decodeURIComponent(url.pathname);
    if (url.origin !== "http://openlab.invalid" || pathname.startsWith("//") ||
        /^\/(login|setup|api|_next)(\/|$)/.test(pathname)) return "/";

    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return "/";
  }
}
