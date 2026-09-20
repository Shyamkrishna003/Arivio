/**
 * Wake the backend before the user needs it.
 *
 * The API is reached through a Vercel rewrite, and a rewrite to an external
 * destination has to start responding within roughly half a minute or Vercel
 * gives up and returns its own error. A free-tier backend that has been idle
 * for fifteen minutes takes longer than that to come back: the container has
 * to start and migrations have to run. Measured cold start on this deployment
 * was about 40 seconds.
 *
 * So the first action a returning visitor takes does not come back slow — it
 * comes back as a proxy error, which looks like a broken site rather than a
 * sleeping one. Retrying works, because by then the backend is up, but the
 * user has already seen the failure.
 *
 * This fires one request at the backend's own hostname, bypassing the rewrite
 * and therefore the proxy's patience, while the user is still reading the
 * landing page. By the time they sign in or scan something, the container is
 * usually awake.
 *
 * Deliberately: no-cors, so it does not depend on the CORS allowlist being
 * right; fire-and-forget, so a failure is silent and never blocks rendering;
 * and skipped entirely when VITE_BACKEND_ORIGIN is unset, which is the case
 * in local development where the backend is already running.
 */

const BACKEND_ORIGIN = import.meta.env.VITE_BACKEND_ORIGIN;

let warmed = false;

export function warmBackend(): void {
  // Once per page load. StrictMode double-invokes effects in development, and
  // there is no reason to send this twice.
  if (warmed || !BACKEND_ORIGIN) return;
  warmed = true;

  // The response is opaque under no-cors and we never read it — the only
  // thing that matters is that the request reached the host and started it.
  void fetch(`${BACKEND_ORIGIN.replace(/\/$/, '')}/health`, {
    mode: 'no-cors',
    cache: 'no-store',
  }).catch(() => {
    // Expected whenever the backend is still starting. The user's real
    // request will arrive later and succeed; there is nothing to report here.
  });
}
