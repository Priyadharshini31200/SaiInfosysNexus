// Pure helpers shared by the Add Staff form and the Access & Permissions tab. The UI state is one object:
//   { assigned: [project codes], module_grants: {module: [actions]}, tally_grants: {code: [actions]}, visibility: [project codes] }
// Nothing is saved until the operator presses Save; switching project in the editor never loses pending changes.
export const EMPTY_PERM = { assigned: [], module_grants: {}, tally_grants: {}, visibility: [] };

export function fromStaff(u) {
  if (!u) return { ...EMPTY_PERM };
  return {
    assigned: u.role === 'USER' ? [...u.report_projects] : u.role === 'DEVELOPER' ? [...(u.developer_projects || [])] : [...u.projects],
    module_grants: Object.fromEntries(Object.entries(u.module_grants || {}).map(([k, v]) => [k, [...v]])),
    tally_grants: Object.fromEntries(Object.entries(u.tally_grants || {}).map(([k, v]) => [k, [...v]])),
    visibility: [...(u.visibility_projects || [])],
  };
}

const sortObj = (o) => Object.fromEntries(Object.keys(o).sort().map((k) => [k, [...o[k]].sort()]).filter(([, v]) => v.length));

/** Stable representation used to detect unsaved changes. */
export const fingerprint = (p) => JSON.stringify({ a: [...p.assigned].sort(), m: sortObj(p.module_grants), t: sortObj(p.tally_grants), v: [...p.visibility].sort() });

/** What the server receives: only grants of projects whose access is switched ON are sent (a disabled project grants nothing). */
export function toPayload(p, role) {
  const on = new Set(p.assigned);
  const owner = (module) => module.split(':')[0];
  if (role === 'USER') return { module_grants: {}, tally_grants: {}, visibility_projects: [], report_projects: [...on], developer_projects: [] };
  if (role === 'DEVELOPER') return { module_grants: {}, tally_grants: {}, visibility_projects: [], report_projects: [], developer_projects: [...on] };
  return {
    module_grants: Object.fromEntries(Object.entries(p.module_grants).filter(([m, a]) => on.has(owner(m)) && a.length)),
    tally_grants: Object.fromEntries(Object.entries(p.tally_grants).filter(([c, a]) => on.has(c) && a.length)),
    visibility_projects: p.visibility.filter((c) => on.has(c)),
    report_projects: [],
    developer_projects: [],
  };
}

/** Every (bucket, key, action) that can be granted for a project: used for counts and Select all. */
export function slots(project) {
  if (project.kind === 'tally') return project.menus.flatMap((m) => m.actions.map((a) => ({ bucket: 'tally_grants', key: project.code, action: a })));
  return project.menus.flatMap((m) => m.actions.map((a) => ({ bucket: 'module_grants', key: m.module, action: a })));
}

export const has = (p, bucket, key, action) => (p[bucket][key] || []).includes(action);

export function countSelected(p, project) {
  const base = slots(project).filter((s) => has(p, s.bucket, s.key, s.action)).length;
  return { selected: base + (p.visibility.includes(project.code) ? 1 : 0), total: slots(project).length + 1 };
}

export function setAction(p, { bucket, key, action }, on) {
  const cur = p[bucket][key] || [];
  const next = on ? [...new Set([...cur, action])] : cur.filter((a) => a !== action);
  return { ...p, [bucket]: { ...p[bucket], [key]: next } };
}

export function setAll(p, project, on) {
  let out = { ...p, visibility: on ? [...new Set([...p.visibility, project.code])] : p.visibility.filter((c) => c !== project.code) };
  for (const s of slots(project)) out = setAction(out, s, on);
  return out;
}

export const toggleAssigned = (p, code, on) => ({ ...p, assigned: on ? [...new Set([...p.assigned, code])] : p.assigned.filter((c) => c !== code) });
