import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { dashboardApi } from '../api/dashboard';
import { registrationsApi } from '../api/registrations';
import { tallyApi } from '../api/tallysl';
import { onVisibilityChanged } from '../utils/cardVisibility';
import Alert from '../components/Alert';
import ProjectUsageCard from '../components/ProjectUsageCard';
import { useAuth } from '../context/AuthContext';
import { useTallyAccess } from '../context/TallyAccessContext';
import { monthKey } from '../utils/date';
import { integrationsApi } from '../api/integrations';
import styles from './OverallDashboard.module.css';

const PRN = 'prn-auto-print';
const MAX_ROWS = 5000; // same safety bound as the Tally reports
const TILES = [
  { key: 'total', label: 'Total Customers' },
  { key: 'active', label: 'Active Licenses' },
  { key: 'trial', label: 'Trial Licenses' },
  { key: 'expired', label: 'Expired Licenses' },
  { key: 'pending', label: 'Pending Activations' },
  { key: 'month', label: 'This Month Expiries' },
];
const ZERO = { active: 0, trial: 0, expired: 0, pending: 0, month: 0 };

/*
 * OVERALL FIGURES (top row) - all from the products' own read APIs, nothing hard-coded:
 *   Total Customers      unique customer identities across all products: a person registered in two products counts once.
 *                        Identity = the e-mail address (case-insensitive); a record without an e-mail is its own identity.
 *   License metrics      counted per product ENTITLEMENT (one row of a product = one licence), so a person holding licences in
 *                        two products contributes to both:
 *     Active Licenses    state "active" and not a trial (1 Year / Lifetime)
 *     Trial Licenses     state "active" and a trial
 *     Expired Licenses   state "expired" (trial or 1 Year past its end)
 *     Pending Activations PRN: first-activation requests waiting for approval; Tally SL: registered, licence not yet activated
 *     This Month Expiries expiry date inside the current calendar month (IST), any state; Lifetime has none
 *   Inactive / cancelled licences are in Total only.
 */
function accumulate(rows, view, product, identities, pending) {
  const thisMonth = monthKey();
  const out = { ...ZERO, pending };
  for (const row of rows) {
    const { state, trial, expiry, email } = view(row);
    identities.add(email ? `e:${email.trim().toLowerCase()}` : `p:${product}:${row.id}`);
    if (state === 'active') out[trial ? 'trial' : 'active'] += 1;
    else if (state === 'expired') out.expired += 1;
    else if (state === 'pending' && pending === null) out.pending += 1;
    if (expiry && monthKey(expiry) === thisMonth) out.month += 1;
  }
  return out;
}

const prnView = (c) => ({ state: c.state, trial: c.license_type === 'TRIAL', expiry: c.effective_expires_at, email: c.email });
const tallyView = (c) => ({ state: c.state, trial: c.license_plan === 'TRIAL', expiry: c.license_plan === 'LIFETIME' ? null : c.expires_at, email: c.email });

async function loadAllTally(code) {
  let rows = [];
  for (let page = 1; ; page += 1) {
    const d = await tallyApi.customers(code, { page, page_size: 100 });
    rows = rows.concat(d.results);
    if (rows.length >= Math.min(d.total, MAX_ROWS) || d.results.length === 0) return rows;
  }
}

const MOTHYS = 'MOTHYS';

/** Common dashboard: overall Nexus summary on top, then one Used / Not Used / Expire card per authorised product. */
export default function OverallDashboard() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const { loaded, can: canTally } = useTallyAccess();
  const [state, setState] = useState({ loading: true, error: '', overall: null, cards: [] });
  const [retry, setRetry] = useState(0);
  // Card visibility: server-computed effective flags, refreshed every 30 s so a scheduled card appears by itself. Absent = visible.
  const [vis, setVis] = useState({});
  useEffect(() => {
    let current = true;
    const load = () => dashboardApi.cardVisibility().then((d) => current && setVis(d.settings), () => {});
    load();
    const timer = setInterval(load, 30000);
    const off = onVisibilityChanged(load); // gear / Configuration changes apply here immediately
    return () => { current = false; clearInterval(timer); off(); };
  }, []);
  // Mothys Inventory (API-connected, TALLY SL PRODUCTS group): its own Used / Not Used / Expire from the project-scoped API.
  // Super Admin only. While loading nothing is shown; on failure the card shows "unavailable", never a false zero.
  const { user } = useAuth();
  const [mothys, setMothys] = useState(null);
  useEffect(() => {
    if (user?.role !== 'SUPERADMIN') return undefined;
    let current = true;
    const label = 'Mothys Inventory Management';
    integrationsApi.productDashboard(MOTHYS).then(
      (d) => current && setMothys({ code: MOTHYS, label, available: true, used: d.used, not_used: d.not_used, expired: d.expired, kind: 'tally' }),
      (e) => current && setMothys({ code: MOTHYS, label, available: false, message: `${label} data is currently unavailable.`, kind: 'tally' }),
    );
    return () => { current = false; };
  }, [user?.role, retry]);
  const canPrn = can('view', PRN) || can('view', `${PRN}:dashboard`);
  const canPrnCard = can('view', `${PRN}:dashboard`);

  useEffect(() => {
    if (!loaded) return undefined;
    let current = true;
    (async () => {
      const errors = [];
      const identities = new Set();
      const sum = { ...ZERO };
      let pendingKnown = false;
      let total = 0;
      const tallyCards = [];
      let prnCard = null;
      const add = (stats, count) => {
        total += count;
        for (const k of Object.keys(ZERO)) if (stats[k] !== null) sum[k] += stats[k];
        if (stats.pending !== null) pendingKnown = true;
      };

      // Tally SL products: the registry and their Used / Not Used / Expire come from the authorised dashboard API; licence metrics from the product's customers.
      let tallyProducts = [];
      try {
        tallyProducts = (await tallyApi.dashboard()).products;
      } catch (err) {
        errors.push(`Tally SL: ${err.message}`);
      }
      for (const p of tallyProducts) {
        tallyCards.push({ code: p.code, label: p.name, available: true, used: p.used, not_used: p.not_used, expired: p.expired, kind: 'tally' });
        try {
          const rows = await loadAllTally(p.code);
          add(accumulate(rows, tallyView, p.code, identities, null), rows.length);
        } catch (err) {
          errors.push(`${p.name}: ${err.message}`);
        }
      }

      // Barcode AutoPrint: existing read-only APIs; the Used / Not Used / Expire counts are the existing dashboard's own, untouched.
      if (canPrn) {
        try {
          const [{ results }, usage] = await Promise.all([registrationsApi.list(PRN), dashboardApi.projectUsage().catch(() => null)]);
          let pending = null;
          try {
            const t = await registrationsApi.deviceTransfers(PRN, 'pending');
            pending = t.results.filter((r) => !r.kind || r.kind === 'FIRST_ACTIVATION').length;
          } catch {
            pending = null; // no approval permission: shown as "—", never a guessed 0
          }
          add(accumulate(results, prnView, PRN, identities, pending), results.length);
          const card = usage?.projects?.find((p) => p.code === PRN);
          if (card && canPrnCard) prnCard = { ...card, kind: 'prn' };
        } catch (err) {
          errors.push(`Barcode AutoPrint: ${err.message}`);
        }
      }
      // UNIVERSE projects (approved + enabled): extra project cards and licence rows for the same six tiles. Legacy products are not touched.
      let uniCards = [];
      if (user?.role === 'SUPERADMIN' || user?.role === 'ADMIN') {
        try {
          const u = await integrationsApi.universeCards();
          uniCards = u.cards;
          if (u.rows.length) add(accumulate(u.rows, (r) => ({ state: r.state, trial: r.trial, expiry: r.expiry, email: r.email }), 'universe', identities, null), u.rows.length);
        } catch {
          /* UNIVERSE data is optional: the legacy dashboard never depends on it */
        }
      }
      if (current) {
        setState({
          loading: false,
          error: errors.join(' · '),
          overall: { ...sum, total: identities.size, pending: pendingKnown ? sum.pending : null },
          cards: [...tallyCards, ...(prnCard ? [prnCard] : []), ...uniCards], // Mothys is merged at render time (below)
        });
      }
    })();
    return () => {
      current = false;
    };
  }, [loaded, canPrn, canPrnCard, retry, user?.role]);

  const open = useCallback((code, category) => {
    if (state.cards.some((c) => c.code === code && c.kind === 'universe')) {
      navigate(`/universe/projects/${code}/customers?${category === 'expired' ? 'status=Expired' : `usage=${category === 'used' ? 'used' : 'not_used'}`}`);
      return;
    }
    if (code === MOTHYS) {
      navigate(`/product/${MOTHYS}/customers?${new URLSearchParams({ filter: category.replace('-', '_') })}`); // used | not_used | expired
      return;
    }
    if (code === PRN) {
      // existing Barcode AutoPrint drill-downs, unchanged
      const query = category === 'expired' ? { product: code, state: 'expired' } : { project: code, status: category };
      navigate(`${category === 'expired' ? '/reports/license-expiry' : '/reports/usage'}?${new URLSearchParams(query)}`);
      return;
    }
    // Tally SL: Used / Not Used -> User Performance report filtered by usage; Expire -> License Expiry report filtered to expired
    if (category === 'expired') navigate(`/tally/reports/license-expiry?${new URLSearchParams({ product: code, status: 'expired' })}`);
    else navigate(`/tally/reports/user-performance?${new URLSearchParams({ product: code, usage: category === 'used' ? 'used' : 'not_used' })}`);
  }, [navigate, state.cards]);
  const canOpen = useCallback((code, category) => (code === MOTHYS || state.cards.some((c) => c.code === code && c.kind === 'universe') ? true : code === PRN ? can('reports', `${PRN}:${category === 'expired' ? 'expiry' : 'usage'}`) : canTally('reports', code)), [can, canTally, state.cards]);

  if (loaded && !canPrn && state.cards.length === 0 && !state.loading) {
    return <section className={styles.page} aria-label="Dashboard"><p className={styles.note}>No Dashboard access is assigned. Choose an authorized menu or contact your Superadmin.</p></section>;
  }
  const cards = mothys ? [...state.cards.filter((c) => c.kind === 'tally'), mothys, ...state.cards.filter((c) => c.kind !== 'tally')] : state.cards;
  const visibleCards = cards.filter((c) => vis[c.code]?.effective !== false);
  const { overall } = state;

  return (
    <section className={styles.page} aria-label="Dashboard">
      {state.error && <Alert type="error">{state.error} <button type="button" className={styles.retry} onClick={() => { setState((s) => ({ ...s, loading: true })); setRetry((n) => n + 1); }}>Retry</button></Alert>}
      {state.loading && <p className={styles.note} role="status">Loading…</p>}

      {!state.loading && overall && (
        <>
          <div className={styles.tiles} role="list" aria-label="Overall Nexus statistics">
            {TILES.map((t) => (
              <div key={t.key} role="listitem" className={styles.tile} {...(t.key === 'month' ? { onClick: () => navigate('/reports/consolidated-expiry'), style: { cursor: 'pointer' }, title: 'Open the Consolidated This Month Expiry report' } : {})}>
                <span className={styles.tileLabel}>{t.label}</span>
                <span className={styles.tileValue}>{overall[t.key] === null ? '—' : overall[t.key].toLocaleString('en-IN')}</span>
              </div>
            ))}
          </div>

          <div className={styles.projects} aria-label="Projects">
            {visibleCards.map((project) => (
              <ProjectUsageCard key={project.code} project={project} selected={null} onSelect={open} canOpen={(category) => canOpen(project.code, category)}
                visibility={vis[project.code] ?? (user?.role === 'SUPERADMIN' ? { code: project.code, mode: 'show', effective: true, version: 0, can_edit: true } : null)}
                onVisibilitySaved={(saved) => setVis((v) => ({ ...v, [saved.code]: saved }))} />
            ))}
            {visibleCards.length === 0 && !state.error && <p className={styles.note}>No products to show.</p>}
          </div>
        </>
      )}
    </section>
  );
}
