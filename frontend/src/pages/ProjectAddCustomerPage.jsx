import { useEffect, useState } from 'react';
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { registrationsApi } from '../api/registrations';
import Alert from '../components/Alert';
import CustomerForm from '../components/CustomerForm';
import { useAuth } from '../context/AuthContext';
import { PROJECT_PAGES, projectBySlug } from '../layouts/navigation';
import styles from './ProjectPage.module.css';

/** Add Customer (and Edit Customer, which reuses it) for one project: the same page for every project. */
export default function ProjectAddCustomerPage() {
  const { slug, id } = useParams();
  const project = projectBySlug(slug);
  if (!project) return <Navigate to="/dashboard" replace />;
  return <AddOrEdit key={`${slug}-${id ?? 'new'}`} project={project} id={id} />;
}

function AddOrEdit({ project, id }) {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const requestId = search.get('request');
  const editing = Boolean(id);
  const allowed = editing ? can('edit') : can('entry');
  const [state, setState] = useState({ loading: (editing || Boolean(requestId)) && allowed, error: '', customer: null, prefill: null });
  const listPath = PROJECT_PAGES.list.path(project.slug);

  useEffect(() => {
    if (!allowed || !(editing || requestId)) return undefined;
    let current = true;
    const load = editing
      ? registrationsApi.get(project.slug, id).then((customer) => ({ customer, prefill: null }))
      : // Add Customer opened from a sign-up request: prefill its details (it must still be pending).
        registrationsApi.requests(project.slug).then(({ results }) => ({
          customer: null,
          prefill: results.find((r) => String(r.id) === requestId) ?? null,
        }));
    load
      .then((loaded) => current && setState({ loading: false, error: '', ...loaded }))
      .catch((e) => current && setState({ loading: false, error: e.message, customer: null, prefill: null }));
    return () => {
      current = false;
    };
  }, [project.slug, id, editing, allowed, requestId]);

  if (!allowed) {
    return <p className={styles.note}>{editing ? 'You do not have permission to edit customers.' : 'You do not have permission to add customers.'}</p>;
  }
  if (state.loading) return <p className={styles.note}>Loading…</p>;
  if (state.error) return <Alert type="error">{state.error}</Alert>;
  if (requestId && !editing && !state.prefill) return <Alert type="error">This registration request has already been approved or rejected.</Alert>;

  return (
    <CustomerForm
      project={project.slug}
      customer={state.customer}
      prefill={state.prefill}
      onSaved={(saved, wasEditing) => {
        let notice = `${saved.name} updated.`;
        let emailFailed = null;
        if (!wasEditing) {
          const sent = saved.credentials_email_status === 'accepted';
          notice = sent ? `Customer Saved & Email Sent: ${saved.name}.` : `Customer saved: ${saved.name}.`;
          if (!sent) emailFailed = { id: saved.id, name: saved.name, notConfigured: saved.credentials_email_status === 'not_delivered' };
        }
        if (!wasEditing && saved.serial_reassigned_from) notice += ` Serial number ${saved.serial_reassigned_from} was already in use, so ${saved.serial_number} was issued instead.`;
        navigate(listPath, { state: { notice, emailFailed } });
      }}
      onCancel={() => navigate(listPath)}
    />
  );
}
