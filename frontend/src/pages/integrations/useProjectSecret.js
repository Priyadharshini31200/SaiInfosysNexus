import { useLayoutEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { useLocation } from 'react-router-dom';
import { takeProjectSecret } from './projectSecretHandoff';

export function useSecretRequestGuard() {
  const { key } = useLocation();
  const revision = useRef(0);
  useLayoutEffect(() => {
    const invalidate = () => { revision.current += 1; };
    window.addEventListener('pagehide', invalidate);
    return () => {
      invalidate();
      window.removeEventListener('pagehide', invalidate);
    };
  }, [key]);
  return revision;
}

export function useProjectSecret(code, acceptInitial = false) {
  const { key } = useLocation();
  const revision = useSecretRequestGuard();
  const [reveal, setReveal] = useState(null);
  const rotationPending = useRef(false);

  useLayoutEffect(() => {
    // Remove credentials left in this history entry by earlier versions.
    const entry = window.history.state;
    if (entry?.usr?.credentials?.api_secret) {
      const usr = { ...entry.usr };
      delete usr.credentials;
      window.history.replaceState({ ...entry, usr }, '');
    }
    const initial = acceptInitial ? takeProjectSecret(code) : '';
    setReveal((current) => {
      if (initial) return { key, code, secret: initial };
      return current?.key === key && current.code === code ? current : null;
    });
    const clearPage = () => flushSync(() => setReveal(null));
    window.addEventListener('pagehide', clearPage);
    return () => window.removeEventListener('pagehide', clearPage);
  }, [acceptInitial, code, key]);

  useLayoutEffect(() => {
    if (!reveal) return undefined;
    const timeout = setTimeout(() => setReveal(null), 120000);
    return () => clearTimeout(timeout);
  }, [reveal]);

  const clear = () => {
    revision.current += 1;
    setReveal(null);
  };
  const rotate = async (issue) => {
    if (rotationPending.current) return false;
    clear();
    const request = revision.current;
    rotationPending.current = true;
    try {
      const response = await issue();
      if (revision.current !== request || !response) return false;
      setReveal({ key, code, secret: response.credentials.api_secret });
      return true;
    } finally {
      rotationPending.current = false;
    }
  };
  return {
    secret: reveal?.key === key && reveal.code === code ? reveal.secret : '',
    clear,
    consume: () => setReveal((current) => current === reveal ? null : current),
    rotate,
  };
}
