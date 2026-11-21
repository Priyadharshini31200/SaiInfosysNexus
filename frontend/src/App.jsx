import ProjectEditPage from './pages/universe/ProjectEditPage';
import ConsolidatedReportsPage from './pages/ConsolidatedReportsPage';
import ProjectDirectoryPage from './pages/ProjectDirectoryPage';
import { Navigate, Route, Routes, useLocation, useParams, useSearchParams } from 'react-router-dom';
import AppLayout from './layouts/AppLayout';
import { PROJECT_MANAGEMENT_ENABLED } from './layouts/navigation';
import { ProtectedRoute, PublicOnlyRoute } from './routes/guards';
import Login from './pages/Login';
import OverallDashboard from './pages/OverallDashboard';
import ProjectAddCustomerPage from './pages/ProjectAddCustomerPage';
import ProjectCustomersPage from './pages/ProjectCustomersPage';
import TallyDashboardPage from './pages/tally/TallyDashboardPage';
import TallyUsageListPage from './pages/tally/TallyUsageListPage';
import TallyCustomersPage from './pages/tally/TallyCustomersPage';
import TallyTransfersPage from './pages/tally/TallyTransfersPage';
import ProjectListPage from './pages/integrations/ProjectListPage';
import ManualCustomerEditPage from './pages/universe/ManualCustomerEditPage';
import ManualCustomerFormPage from './pages/universe/ManualCustomerFormPage';
import ProjectFormPage from './pages/integrations/ProjectFormPage';
import ProjectIntegrationPage from './pages/integrations/ProjectIntegrationPage';
import IntegrationStatusPage from './pages/integrations/IntegrationStatusPage';
import ProductDashboardPage from './pages/product/ProductDashboardPage';
import ProductCustomersPage from './pages/product/ProductCustomersPage';
import NotFound from './pages/NotFound';
import SetupPasswordPage from './pages/SetupPasswordPage';
import DeveloperCredentialsPage from './pages/DeveloperCredentialsPage';
import ReportViewerShell from './layouts/ReportViewerShell';
import DeveloperShell from './layouts/DeveloperShell';
import UniverseDashboardPage from './pages/universe/UniverseDashboardPage';
import ProjectWorkspacePage from './pages/universe/ProjectWorkspacePage';
import ApiIntegrationPage from './pages/universe/ApiIntegrationPage';
import UniverseCustomersPage from './pages/universe/UniverseCustomersPage';
import { useAuth } from './context/AuthContext';

// A bare project URL opens its Customer List.
/** + Add Project: Non-Tally opens the manual (Barcode-style) customer form that also creates the project; Tally opens the four-field Add / Connect Project form. */
function AddProjectEntry() {
  const [qs] = useSearchParams();
  return qs.get('category') === 'non-tally' ? <ManualCustomerFormPage /> : <ProjectFormPage />;
}

function ProjectRedirect() {
  const { slug } = useParams();
  return <Navigate to={`/projects/${slug}/customers`} replace />;
}

// Old per-project UNIVERSE links (bell, cards) land in the project workspace.
function LegacyUniverseLink({ tab }) {
  const { code } = useParams();
  const { search } = useLocation();
  return <Navigate to={`/universe/projects/${code}/${tab}${search}`} replace />;
}

function ReportRedirect({ report, project }) {
  const { search } = useLocation();
  const params = new URLSearchParams(search);
  params.set('report', report);
  if (!params.has('project') && project) params.set('project', params.get('product') || project);
  return <Navigate to={`/reports?${params}`} replace />;
}

// A restricted User (Report Viewer) gets a one-report shell instead of the application layout; every other path is unreachable for them.
function RoleLayout() {
  const { user } = useAuth();
  if (user?.role === 'DEVELOPER') return <DeveloperShell />;
  return user?.role === 'USER' ? <ReportViewerShell /> : <AppLayout />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/setup-password" element={<SetupPasswordPage />} />
      <Route path="/setup-customer-password" element={<SetupPasswordPage customer />} />
      <Route path="/developer-credentials" element={<DeveloperCredentialsPage />} />
      <Route element={<PublicOnlyRoute />}>
        <Route path="/login" element={<Login />} />
      </Route>

      <Route element={<ProtectedRoute />}>
        <Route element={<RoleLayout />}>
          <Route path="/dashboard" element={<OverallDashboard />} />
          <Route path="/projects/:slug" element={<ProjectRedirect />} />
          <Route path="/projects/:slug/add" element={<ProjectAddCustomerPage />} />
          <Route path="/projects/:slug/customers" element={<ProjectCustomersPage />} />
          <Route path="/projects/:slug/customers/:id/edit" element={<ProjectAddCustomerPage />} />
          <Route path="/reports" element={<ConsolidatedReportsPage />} />
          <Route path="/reports/license-expiry" element={<ReportRedirect report="expiry" />} />
          <Route path="/reports/usage" element={<ReportRedirect report="performance" />} />
          <Route path="/tally/reports/license-expiry" element={<ReportRedirect report="expiry" project="gstr2tally" />} />
          <Route path="/tally/reports/tally-details" element={<ReportRedirect report="tally" project="gstr2tally" />} />
          <Route path="/tally/reports/user-performance" element={<ReportRedirect report="performance" project="gstr2tally" />} />
          <Route path="/tally/:product/dashboard" element={<TallyDashboardPage />} />
          <Route path="/tally/:product/usage/:category" element={<TallyUsageListPage />} />
          <Route path="/tally/:product/transfers" element={<TallyTransfersPage />} />
          <Route path="/tally/:product/customers" element={<TallyCustomersPage />} />
          <Route path="/tally/:product/customers/:id" element={<TallyCustomersPage />} /> /* the Customer Details drawer opens over the list */
          <Route path="/product/:code/dashboard" element={<ProductDashboardPage />} />
          <Route path="/product/:code/customers" element={<ProductCustomersPage />} />
          <Route path="/product/:code/customers/:id" element={<ProductCustomersPage />} />
          <Route path="/mothys/reports/license-expiry" element={<ReportRedirect report="expiry" project="MOTHYS" />} />
          <Route path="/mothys/reports/customer-details" element={<ReportRedirect report="expiry" project="MOTHYS" />} />
          <Route path="/mothys/reports/user-performance" element={<ReportRedirect report="performance" project="MOTHYS" />} />
          {PROJECT_MANAGEMENT_ENABLED ? (
            <>
              <Route path="/project-management" element={<ProjectListPage />} />
              <Route path="/project-management/add" element={<ProjectFormPage />} />
              <Route path="/project-management/status" element={<IntegrationStatusPage />} />
              <Route path="/project-management/:code/integration" element={<ProjectIntegrationPage />} />
            </>
          ) : (
            <Route path="/project-management/*" element={<Navigate to="/dashboard" replace />} />
          )}
          <Route path="/directory/tally" element={<ProjectDirectoryPage key="tally" kind="tally" />} />
          <Route path="/directory/non-tally" element={<ProjectDirectoryPage key="non-tally" kind="non-tally" />} />
          <Route path="/universe" element={<Navigate to="/universe/api" replace />} />
          <Route path="/universe/projects" element={<Navigate to="/directory/tally" replace />} />
          <Route path="/universe/projects/add" element={<AddProjectEntry />} />
          <Route path="/universe/projects/:code/edit" element={<ProjectEditPage />} />
          <Route path="/universe/projects/:code/customers/add" element={<ManualCustomerFormPage />} />
          <Route path="/universe/projects/:code/customers/:id/edit" element={<ManualCustomerEditPage />} />
          <Route path="/universe/projects/:code" element={<ProjectWorkspacePage />}>
            <Route index element={<Navigate to="dashboard" replace />} />
            <Route path="dashboard" element={<UniverseDashboardPage />} />
            <Route path="customers" element={<UniverseCustomersPage />} />
          </Route>
          <Route path="/universe/api" element={<ApiIntegrationPage />} />
          <Route path="/universe/logs" element={<Navigate to="/universe/api?tab=logs" replace />} />
          <Route path="/universe/:code/dashboard" element={<LegacyUniverseLink tab="dashboard" />} />
          <Route path="/universe/:code/customers" element={<LegacyUniverseLink tab="customers" />} />
          <Route path="/reports/consolidated-expiry" element={<ReportRedirect report="expiry" />} />
          <Route path="/profile" element={<Navigate to="/dashboard" replace />} />
          <Route path="/reset-password" element={<Navigate to="/dashboard" replace />} />
        </Route>
      </Route>

      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
