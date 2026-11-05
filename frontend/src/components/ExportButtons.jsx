import { useEffect, useRef, useState } from 'react';
import { openPrintWindow, saveFile } from '../utils/exporter';
import { ChevronDownIcon, DownloadIcon, FileTextIcon, PrinterIcon } from './Icons';
import styles from './ExportButtons.module.css';

const ACTIONS = [
  { fmt: 'xlsx', label: 'Excel', hint: 'Download .xlsx', icon: DownloadIcon },
  { fmt: 'pdf', label: 'PDF', hint: 'Download .pdf', icon: FileTextIcon },
  { fmt: 'print', label: 'Print', hint: 'Open print preview', icon: PrinterIcon },
];

/**
 * Excel / PDF / Print of the rows on screen. `run(fmt)` asks the server (which checks the permission) for the file or print data.
 * `menu` shows one Export dropdown instead of three buttons.
 */
export default function ExportButtons({ run, disabled, onError, menu = false }) {
  const [busy, setBusy] = useState(null);
  const [open, setOpen] = useState(false);
  const root = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => !root.current?.contains(e.target) && setOpen(false);
    const onKey = (e) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  async function go(fmt) {
    setOpen(false);
    setBusy(fmt);
    const printer = fmt === 'print' ? openPrintWindow() : null;
    try {
      const result = await run(fmt);
      if (printer) printer.finish(result);
      else saveFile(result);
    } catch (err) {
      printer?.fail();
      onError?.(err.message || 'Export failed.');
    } finally {
      setBusy(null);
    }
  }

  if (menu) {
    return (
      <div className={styles.menuRoot} ref={root}>
        <button
          type="button"
          className={styles.btn}
          onClick={() => setOpen((v) => !v)}
          disabled={disabled || busy !== null}
          aria-haspopup="menu"
          aria-expanded={open}
        >
          <DownloadIcon size={16} /> {busy ? 'Exporting…' : 'Export'} <ChevronDownIcon size={14} />
        </button>
        {open && (
          <ul className={styles.menu} role="menu">
            {ACTIONS.map(({ fmt, label, hint, icon: Icon }) => (
              <li key={fmt} role="none">
                <button type="button" role="menuitem" className={styles.item} onClick={() => go(fmt)}>
                  <Icon size={16} />
                  <span>
                    <strong>{label}</strong>
                    <small>{hint}</small>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  return (
    <div className={styles.group} role="group" aria-label="Export">
      {ACTIONS.map(({ fmt, label, icon: Icon }) => (
        <button key={fmt} type="button" className={styles.btn} onClick={() => go(fmt)} disabled={disabled || busy !== null} aria-busy={busy === fmt}>
          <Icon size={16} /> {busy === fmt ? '…' : label}
        </button>
      ))}
    </div>
  );
}
