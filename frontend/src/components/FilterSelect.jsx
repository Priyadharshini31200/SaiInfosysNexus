import styles from './CustomerList.module.css';

/** A compact labelled dropdown for list filters. `options` is [{ value, label }]; the first one is normally "All". */
export default function FilterSelect({ label, value, onChange, options, disabled }) {
  return (
    <label className={styles.filter}>
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} aria-label={label}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
