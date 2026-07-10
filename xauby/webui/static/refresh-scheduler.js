/**
 * Runs a single refresh at a time and pauses background polling in hidden tabs.
 * The caller owns rendering; this module owns only the refresh lifecycle.
 */
export function createRefreshScheduler(refresh, { intervalMs = 5000, onError = () => {} } = {}) {
  let timerId = null;
  let stopped = false;
  let inFlight = false;

  const schedule = (delay = intervalMs) => {
    if (stopped || document.hidden) return;
    window.clearTimeout(timerId);
    timerId = window.setTimeout(run, delay);
  };

  const run = async () => {
    if (stopped || document.hidden || inFlight) return;
    inFlight = true;
    try {
      await refresh();
    } catch (error) {
      onError(error);
    } finally {
      inFlight = false;
      schedule();
    }
  };

  const onVisibilityChange = () => {
    if (document.hidden) {
      window.clearTimeout(timerId);
    } else {
      schedule(0);
    }
  };

  document.addEventListener("visibilitychange", onVisibilityChange);

  return {
    start() {
      schedule(0);
    },
    stop() {
      stopped = true;
      window.clearTimeout(timerId);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    },
  };
}
