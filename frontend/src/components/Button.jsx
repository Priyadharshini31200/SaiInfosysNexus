import styles from './Button.module.css';

export default function Button({ children, variant = 'primary', loading = false, loadingLabel, disabled, type = 'button', block = false, ...rest }) {
  return (
    <button
      type={type}
      className={`${styles.btn} ${styles[variant]} ${block ? styles.block : ''}`}
      disabled={disabled || loading}
      aria-busy={loading}
      {...rest}
    >
      {loading && <span className={styles.spinner} aria-hidden="true" />}
      <span>{loading && loadingLabel ? loadingLabel : children}</span>
    </button>
  );
}
