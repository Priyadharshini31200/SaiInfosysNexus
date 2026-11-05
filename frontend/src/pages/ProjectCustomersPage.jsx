import { useCallback, useEffect, useState } from 'react';
import { Navigate, useLocation, useNavigate, useParams } from 'react-router-dom';
import { registrationsApi } from '../api/registrations';
import Alert from '../components/Alert';
import Button from '../components/Button';
import CustomerList from '../components/CustomerList';
import RegistrationRequests from '../components/RegistrationRequests';
import Toast from '../components/Toast';
import { useAuth } from '../context/AuthContext';
import useSyncChanges from '../utils/useSyncChanges';
import { PROJECT_PAGES, projectBySlug } from '../layouts/navigation';
import styles from './ProjectPage.module.css';

/** Customer List of one project (the same page for every project). */
export default function ProjectCustomersPage() {
  const { slug } = useParams();
  const project = projectBySlug(slug);
  if (!project) return <Navigate to="/dashboard" replace />;
  return <Customers key={slug} project={project} />; // key: switching project starts from a clean state
}

function Customers({ project }) {
  const { can } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const canView = can('view');
  const [state, setState] = useState({ loading: canView, error: '', customers: null });
  const [requests, setRequests] = useState([]);
  const synced = useSyncChanges(() => registrationsApi.sync(project.slug), canView); // other sessions' changes
  const canEntry = can('entry');
  const [toast, setToast] = useState(null);
  const clearToast = useCallback(() => setToast(null), []);
  const notify = useCallback((type, text) => setToast({ id: Date.now(), type, text }), []);

  // A message handed over by the Add / Edit page ("X registered."), shown once.
  useEffect(() => {
    const notice = location.state?.notice;
    if (!notice) return;
    if (location.state.emailFailed) setEmailFailed(location.state.emailFailed);
    else notify('success', notice);
    navigate(location.pathname, { replace: true, state: null });
  }, [location, navigate, notify]);

  // The customer was saved but the credentials email was not delivered: keep the record, offer Retry Email.
  const [emailFailed, setEmailFailed] = useState(null);
  const [retrying, setRetrying] = useState(false);
  const retryEmail = async () => {
    setRetrying(true);
    try {
      const updated = await registrationsApi.retryEmail(project.slug, emailFailed.id);
      if (updated.email_sent) {
        setEmailFailed(null);
        notify('success', `Customer Saved & Email Sent: ${emailFailed.name}.`);
      } else setEmailFailed((f) => ({ ...f, notConfigured: updated.credentials_email_status === 'not_delivered' }));
      setState((s) => (s.customers ? { ...s, customers: s.customers.map((c) => (c.id === updated.id ? updated : c)) } : s));
    } catch (e) {
      notify('error', e.message);
    } finally {
      setRetrying(false);
    }
  };

  useEffect(() => {
    if (!canView) return undefined;
    let current = true;
    registrationsApi
      .list(project.slug)
      .then(({ results }) => current && setState({ loading: false, error: '', customers: results }))
      .catch((e) => current && setState((s) => (s.customers ? s : { loading: false, error: e.message, customers: null }))); // a failed refresh keeps the list on screen
    return () => {
      current = false;
    };
  }, [project.slug, canView, synced]);

  // Pending sign-ups from the connected project: the notification for whoever may add customers.
  useEffect(() => {
    if (!canEntry) return undefined;
    let current = true;
    registrationsApi
      .requests(project.slug)
      .then(({ results }) => current && setRequests(results))
      .catch(() => current && setRequests([]));
    return () => {
      current = false;
    };
  }, [project.slug, canEntry, synced]);

  const rejectRequest = async (req) => {
    if (!window.confirm(`Reject the registration request from ${req.name} (${req.email})? No license will be created.`)) return;
    try {
      await registrationsApi.rejectRequest(project.slug, req.id);
      setRequests((list) => list.filter((r) => r.id !== req.id));
      notify('success', `Request from ${req.name} rejected.`);
    } catch (e) {
      notify('error', e.message);
    }
  };

  const replace = (customer) =>
    setState((s) => ({ ...s, customers: s.customers.map((c) => (c.id === customer.id ? customer : c)) }));

  return (
    <div className={styles.fill}>
      {emailFailed && (
        <Alert type="error">
          <strong>Email Delivery Failed.</strong> {emailFailed.name} was saved, but the credentials email was not delivered
          {emailFailed.notConfigured ? ' (no mail server is configured)' : ''}.{' '}
          <Button variant="secondary" onClick={retryEmail} loading={retrying} loadingLabel="Sending…">
            Retry Email
          </Button>
        </Alert>
      )}
      <RegistrationRequests project={project.slug} requests={requests} onReject={rejectRequest} />
      <CustomerList
        project={project.slug}
        label={project.label}
        customers={state.customers}
        loading={state.loading}
        error={state.error}
        onChanged={replace}
        onEdit={(customer) => navigate(PROJECT_PAGES.edit.path(project.slug, customer.id))}
        onNotify={notify}
      />
      <Toast toast={toast} onDone={clearToast} />
    </div>
  );
}
