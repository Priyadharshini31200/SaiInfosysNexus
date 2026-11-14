import { DashboardIcon, FolderIcon, PrinterIcon, ReportIcon } from '../components/Icons';

// Sidebar entries. Dashboard is always first; projects are listed under their own section.
export const NAV_ITEMS = [{ to: '/dashboard', label: 'Dashboard', icon: DashboardIcon }];

// The one place a project is declared. `slug` is the project identifier used by the routes and the
// /api/projects/<slug>/ endpoints (and registered in apps/registrations/models.py). Every project listed
// here automatically gets the same Add Customer / Customer List submenu, pages and API client.
export const PROJECT_ITEMS = [
  { slug: 'gstr2tally', label: 'GSTR2Tally', icon: FolderIcon },
  { slug: 'prn-auto-print', label: 'Barcode AutoPrint', icon: PrinterIcon },
];

// The Reports menu (shown to users who may view reports): one expandable item with a page per report.
export const REPORTS_MENU = { label: 'Reports', icon: ReportIcon };
export const REPORT_PAGES = [
  { label: 'License Expiry Report', path: '/reports/license-expiry' },
  { label: 'Usage Report', path: '/reports/usage' },
];
export const USAGE_STATUSES = { used: 'Used', 'not-used': 'Not Used' };

// Sidebar grouping only (a navigation change - the project slug, routes and API of Barcode AutoPrint are unchanged).
// "Non-Tally SL Products" lists Barcode AutoPrint. GSTR2Tally now lives under "Tally SL Products" (apps/tallysl).
export const NON_TALLY_ITEMS = PROJECT_ITEMS.filter((p) => p.slug === 'prn-auto-print');

export const projectBySlug = (slug) => PROJECT_ITEMS.find((p) => p.slug === slug);

export const PROJECT_PAGES = {
  add: { label: 'Add Customer', path: (slug) => `/projects/${slug}/add` },
  list: { label: 'Customer List', path: (slug) => `/projects/${slug}/customers` },
  edit: { label: 'Edit Customer', path: (slug, id) => `/projects/${slug}/customers/${id}/edit` },
};
export const PROJECT_MENU = [PROJECT_PAGES.add, PROJECT_PAGES.list];

/** Navbar title for a path: "Dashboard", or "<Project> · <Page>". */
export function getPageTitle(pathname, search = '') {
  if (pathname === '/dashboard') return 'Dashboard';
  if (pathname.startsWith('/tally/')) {
        if (pathname.startsWith('/tally/reports/')) return `GSTR Reports · ${pathname.endsWith('tally-details') ? 'Tally Details Report' : pathname.endsWith('user-performance') ? 'User Performance Report' : 'License Expiry Report'}`;
    const m = pathname.match(/^\/tally\/([^/]+)\/(dashboard|usage|customers)/);
    const page = { dashboard: 'User Performance', usage: 'User Performance', customers: 'Customers' }[m?.[2]] || '';
    return `Tally SL Products${page ? ` · ${page}` : ''}`;
  }
  if (pathname === '/reports/usage') {
    // "GSTR2Tally Used Report" once the Dashboard has sent a project and a count
    const params = new URLSearchParams(search);
    const project = projectBySlug(params.get('project'));
    const status = USAGE_STATUSES[params.get('status')];
    if (project && status) return `${project.label} ${status} Report`;
    if (project) return `${project.label} Usage Report`;
    return `${REPORTS_MENU.label} · Usage Report`;
  }
  const report = REPORT_PAGES.find((r) => r.path === pathname);
  if (report) return `${REPORTS_MENU.label} · ${report.label}`;
  const match = pathname.match(/^\/projects\/([^/]+)\/(add|customers(?:\/\d+\/edit)?)\/?$/);
  const project = match && projectBySlug(match[1]);
  if (!project) return 'Sai Infosys Nexus';
  const page = match[2] === 'add' ? PROJECT_PAGES.add : match[2] === 'customers' ? PROJECT_PAGES.list : PROJECT_PAGES.edit;
  return `${project.label} · ${page.label}`;
}
