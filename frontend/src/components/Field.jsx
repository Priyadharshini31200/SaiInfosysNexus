import { useId, useState } from 'react';
import { EyeIcon, EyeOffIcon } from './Icons';
import styles from './Field.module.css';

function FieldShell({ id, label, error, hint, compact, required, children }) {
  return (
    <div className={`${styles.field} ${compact ? styles.compact : ''}`}>
      <label htmlFor={id} className={styles.label}>
        {label}
        {required && (
          <span className={styles.required} aria-hidden="true">
            {' '}*
          </span>
        )}
      </label>
      {children}
      {error ? (
        <p id={`${id}-error`} className={styles.error} role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className={styles.hint}>{hint}</p>
      ) : null}
    </div>
  );
}

export function TextField({ label, error, hint, icon, compact, trailing, required, ...inputProps }) {
  const id = useId();
  return (
    <FieldShell id={id} label={label} error={error} hint={hint} compact={compact} required={required}>
      <div className={`${styles.control} ${error ? styles.invalid : ''}`}>
        {icon && <span className={styles.leading}>{icon}</span>}
        <input id={id} className={styles.input} aria-required={required || undefined} aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : undefined} {...inputProps} />
        {trailing}
      </div>
    </FieldShell>
  );
}

export function PasswordField({ label, error, hint, icon, compact, required, ...inputProps }) {
  const id = useId();
  const [visible, setVisible] = useState(false);
  return (
    <FieldShell id={id} label={label} error={error} hint={hint} compact={compact} required={required}>
      <div className={`${styles.control} ${error ? styles.invalid : ''}`}>
        {icon && <span className={styles.leading}>{icon}</span>}
        <input
          id={id}
          aria-required={required || undefined}
          className={styles.input}
          type={visible ? 'text' : 'password'}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? `${id}-error` : undefined}
          {...inputProps}
        />
        <button
          type="button"
          className={styles.toggle}
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? 'Hide password' : 'Show password'}
          aria-pressed={visible}
          title={visible ? 'Hide password' : 'Show password'}
        >
          {visible ? <EyeOffIcon size={19} /> : <EyeIcon size={19} />}
        </button>
      </div>
    </FieldShell>
  );
}

export function SelectField({ label, error, hint, compact, required, children, ...selectProps }) {
  const id = useId();
  return (
    <FieldShell id={id} label={label} error={error} hint={hint} compact={compact} required={required}>
      <div className={`${styles.control} ${error ? styles.invalid : ''}`}>
        <select id={id} aria-required={required || undefined} className={styles.input} aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : undefined} {...selectProps}>
          {children}
        </select>
      </div>
    </FieldShell>
  );
}

export function TextAreaField({ label, error, hint, compact, ...areaProps }) {
  const id = useId();
  return (
    <FieldShell id={id} label={label} error={error} hint={hint} compact={compact}>
      <div className={`${styles.control} ${styles.areaControl} ${error ? styles.invalid : ''}`}>
        <textarea id={id} className={`${styles.input} ${styles.area}`} rows={2} aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : undefined} {...areaProps} />
      </div>
    </FieldShell>
  );
}
