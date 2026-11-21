import styles from './Integrations.module.css';

const PROTOCOLS = [['REST', 'REST API (JSON)'], ['GRAPHQL', 'GraphQL API'], ['SOAP', 'SOAP / XML API'], ['OTHER', 'Other (Specify API Type)']];
export const FRAMEWORKS = ['FastAPI (Python)', 'Django REST Framework', 'Flask (Python)', 'Node.js / Express', 'ASP.NET Core Web API', 'Java / Spring Boot', 'Laravel / PHP', 'Unknown / Not Specified'];

/** Row 2 of API Setup: API Type / Protocol (cfg.api_protocol) and Backend Framework / Technology (project.framework - descriptive only, it never changes API behaviour).
 *  Only REST (JSON) has an adapter; the others are saved as drafts and Nexus refuses to validate, sync or activate them. */
export default function ProtocolFields({ cfg, up, err, framework, setFramework }) {
  const p = cfg.api_protocol || { type: 'REST' };
  const setP = (patch) => up({ api_protocol: { ...p, ...patch } });
  const known = !framework || FRAMEWORKS.includes(framework);
  const fwSelect = known ? framework || '' : 'OTHER';
  const lbl = (text, req) => <span className={styles.fieldLabel}>{text}{req && <b className={styles.req} aria-hidden="true">*</b>}</span>;
  return (
    <>
      <label className={styles.field}>{lbl('API Type / Protocol', true)}
        <select value={p.type} onChange={(e) => setP({ type: e.target.value })}>{PROTOCOLS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>{err('api_protocol')}
      </label>
      <label className={styles.field}>{lbl('Backend Framework / Technology')}
        <select value={fwSelect} onChange={(e) => setFramework(e.target.value === 'OTHER' ? ' ' : e.target.value)}>
          <option value="">Select…</option>{FRAMEWORKS.map((v) => <option key={v} value={v}>{v}</option>)}<option value="OTHER">Other (Specify Framework)</option>
        </select>
      </label>
      {fwSelect === 'OTHER' && (
        <label className={styles.field}>{lbl('Enter Backend Framework / Technology', true)}
          <input maxLength={60} value={framework.trimStart()} onChange={(e) => setFramework(e.target.value || ' ')} placeholder="e.g. NestJS, Go / Gin, Custom ERP API" />
        </label>
      )}
      {p.type === 'OTHER' && (
        <>
          <label className={styles.field}>{lbl('Custom API Type', true)}<input maxLength={120} value={p.custom_type || ''} onChange={(e) => setP({ custom_type: e.target.value })} placeholder="Exact API type from the developer" />{err('api_custom_type')}</label>
          <label className={styles.field}>{lbl('API Description (optional)')}<input maxLength={120} value={p.description || ''} onChange={(e) => setP({ description: e.target.value })} /></label>
          <label className={styles.field}>{lbl('Content Type')}<input maxLength={120} value={p.content_type || ''} onChange={(e) => setP({ content_type: e.target.value })} placeholder="e.g. application/x-protobuf" /></label>
          <label className={styles.field}>{lbl('Request Format')}<input maxLength={120} value={p.request_format || ''} onChange={(e) => setP({ request_format: e.target.value })} /></label>
          <label className={styles.field}>{lbl('Response Format')}<input maxLength={120} value={p.response_format || ''} onChange={(e) => setP({ response_format: e.target.value })} /></label>
        </>
      )}
      {p.type !== 'REST' && <p role="status" className={`${styles.note} ${styles.wide}`}><b>Protocol Not Supported Yet.</b> This configuration can be saved as a draft, but Nexus will not validate, synchronise or activate it until an adapter for this protocol exists. Use REST API (JSON) for a working integration.</p>}
    </>
  );
}
