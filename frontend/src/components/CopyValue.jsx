import { useRef } from 'react';
import { notify } from './TopToast';

/** Read-only value with a copy icon. An optional callback consumes a one-time value after a successful copy. */
export default function CopyValue({ value, label, mono = true, style, onCopied }) {
  const copying = useRef(false);
  const copy = async () => {
    if (onCopied && copying.current) return;
    copying.current = true;
    try { await navigator.clipboard.writeText(value); } catch {
      if (onCopied) {
        copying.current = false;
        notify('Clipboard unavailable. Select and copy the value manually.', 'error');
        return;
      }
      const t = document.createElement('textarea');
      t.value = value;
      document.body.appendChild(t);
      t.select();
      try {
        if (!document.execCommand('copy')) throw new Error('Clipboard unavailable');
      } catch {
        notify('Clipboard unavailable. Select and copy the value manually.', 'error');
        return;
      } finally { t.remove(); }
    }
    onCopied?.();
    notify(`${label} copied.`);
  };
  return (
    <span style={{ display: 'flex', alignItems: 'center', height: 36, boxSizing: 'border-box', border: '1px solid #b9c9e3', borderRadius: 6, background: '#f1f5fb', padding: '0 4px 0 10px', ...style }}>
      <span aria-label={label} style={{ flex: 1, fontFamily: mono ? 'Consolas, monospace' : undefined, fontSize: 13.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value || '—'}</span>
      {value && (
        <button type="button" onClick={copy} title={`Copy ${label}`} aria-label={`Copy ${label}`} style={{ border: 0, background: 'none', cursor: 'pointer', padding: 6, color: '#1d4ed8', display: 'inline-flex' }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>
        </button>
      )}
    </span>
  );
}
