import { useEffect, useRef, useState } from 'react';

const INTERVAL = 10000; // ms between checks

/**
 * Lightweight polling so a list or the dashboard follows changes made in other sessions (Admin <-> Super Admin).
 * `fetchVersion` returns a fingerprint string from a cheap endpoint; the hook returns a number that goes up each time
 * the fingerprint differs from the last one seen, so an effect can depend on it to reload. It does not count the first
 * reading, pauses while the tab is hidden, checks again as soon as the tab is shown, and ignores network errors.
 */
export default function useSyncChanges(fetchVersion, enabled = true) {
  const [changes, setChanges] = useState(0);
  const fetchRef = useRef(fetchVersion);
  fetchRef.current = fetchVersion;

  useEffect(() => {
    if (!enabled) return undefined;
    let stopped = false;
    let timer;
    let last = null;

    const tick = async () => {
      if (document.visibilityState === 'visible') {
        try {
          const { version } = await fetchRef.current();
          if (stopped) return;
          if (last !== null && version !== last) setChanges((n) => n + 1);
          last = version;
        } catch {
          // a missed check is retried on the next tick
        }
      }
      if (!stopped) timer = setTimeout(tick, INTERVAL);
    };
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        clearTimeout(timer);
        tick();
      }
    };

    tick();
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      stopped = true;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [enabled]);

  return changes;
}
