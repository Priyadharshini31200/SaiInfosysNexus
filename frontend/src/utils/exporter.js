const esc = (value) =>
  String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);

export function saveFile({ blob, filename }) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

const PRINT_CSS = `
  @page { size: A4 landscape; margin: 10mm; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: 'Segoe UI', Arial, sans-serif; color: #012641; background: #fff; }
  h1 { margin: 0 0 4px; font-size: 18px; }
  p { margin: 0 0 12px; font-size: 11px; color: #4f6b7d; }
  table { width: 100%; border-collapse: collapse; font-size: ${'__SIZE__'}px; }
  th, td { border: 1px solid #c9dde6; padding: 4px 6px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
  th { background: #012641; color: #fff; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  tbody tr:nth-child(even) td { background: #f4fbfd; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  thead { display: table-header-group; }
  tr { break-inside: avoid; }
`;

export function printHtml({ title, subtitle, columns, rows }) {
  const size = columns.length > 10 ? 8 : 11;
  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(title)}</title><style>${PRINT_CSS.replace('__SIZE__', size)}</style></head><body>
<h1>${esc(title)}</h1><p>${esc(subtitle)}</p>
<table><thead><tr>${columns.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead>
<tbody>${rows.length ? rows.map((r) => `<tr>${r.map((v) => `<td>${esc(v)}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${columns.length}">No records.</td></tr>`}</tbody></table>
</body></html>`;
}

/**
 * Opens a blank window straight away (inside the click, so it is not blocked as a popup), then fills it with the report
 * and prints it. Call finish(data) once the data is here, or fail() if it could not be fetched.
 */
export function openPrintWindow() {
  const win = window.open('', '_blank');
  if (win) win.document.write('<p style="font-family:Segoe UI,Arial,sans-serif;color:#012641">Preparing report…</p>');
  return {
    finish(data) {
      if (win) {
        win.document.open();
        win.document.write(printHtml(data));
        win.document.close();
        win.focus();
        setTimeout(() => win.print(), 250);
        return;
      }
      // Pop-ups blocked: print the same page from a hidden frame instead.
      const frame = document.createElement('iframe');
      frame.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0';
      document.body.appendChild(frame);
      frame.contentDocument.open();
      frame.contentDocument.write(printHtml(data));
      frame.contentDocument.close();
      setTimeout(() => {
        frame.contentWindow.focus();
        frame.contentWindow.print();
        setTimeout(() => frame.remove(), 1000);
      }, 250);
    },
    fail() {
      win?.close();
    },
  };
}
