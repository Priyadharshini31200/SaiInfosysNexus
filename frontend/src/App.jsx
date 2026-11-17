import { Navigate, Route, Routes, useParams } from 'react-router-dom';
import AppLayout from './layouts/AppLayout';
import { ProtectedRoute, PublicOnlyRoute } from './routes/guards';
import Login from './pages/Login';
import OverallDashboard from './pages/OverallDashboard';
import ProjectAddCustomerPage from './pages/ProjectAddCustomerPage';
import ProjectCustomersPage from './pages/ProjectCustomersPage';
import LicenseExpiryReportPage from './pages/LicenseExpiryReportPage';
import UsageReportPage from './pages/UsageReportPage';
import TallyDashboardPage from './pages/tally/TallyDashboardPage';
import TallyUsageListPage from './pages/tally/TallyUsageListPage';
import TallyCustomersPage from './pages/tally/TallyCustomersPage';
import TallyTransfersPage from './pages/tally/TallyTransfersPage';
import TallyReportPage from './pages/tally/TallyReportPage';
import ProjectListPage from './pages/integrations/ProjectListPage';
import ProjectFormPage from './pages/integrations/ProjectFormPage';
import ProjectIntegrationPage from './pages/integrations/ProjectIntegrationPage';
import IntegrationStatusPage from './pages/integrations/IntegrationStatusPage';
import ProductDashboardPage from './pages/product/ProductDashboardPage';
import ProductCustomersPage from './pages/product/ProductCustomersPage';
import ProductReportPage from './pages/product/ProductReportPage';
import NotFound from './pages/NotFound';

// A bare project URL opens its Customer List.
function ProjectRedirect() {
  const { slug } = useParams();
  return <Navigate to={`/projects/${slug}/customers`} replace />;
}

export default function App() {
  return (
    <Routes>
      <Route element={<PublicOnlyRoute />}>
        <Route path="/login" element={<Login />} />
      </Route>

      <Route element={<ProtectedRoute />}>
        <Route element={<AppLayout />}>
          <Route path="/dashboard" element={<OverallDashboard />} />
          <Route path="/projects/:slug" element={<ProjectRedirect />} />
          <Route path="/projects/:slug/add" element={<ProjectAddCustomerPage />} />
          <Route path="/projects/:slug/customers" element={<ProjectCustomersPage />} />
          <Route path="/projects/:slug/customers/:id/edit" element={<ProjectAddCustomerPage />} />
          <Route path="/reports/license-expiry" element={<LicenseExpiryReportPage />} />
          <Route path="/reports/usage" element={<UsageReportPage />} />
          <Route path="/tally/reports/license-expiry" element={<TallyReportPage kind="expiry" />} />
          <Route path="/tally/reports/tally-details" element={<TallyReportPage kind="tally" />} />
          <Route path="/tally/reports/user-performance" element={<TallyReportPage kind="performance" />} />
          <Route path="/tally/:product/dashboard" element={<TallyDashboardPage />} />
          <Route path="/tally/:product/usage/:category" element={<TallyUsageListPage />} />
          <Route path="/tally/:product/transfers" element={<TallyTransfersPage />} />
          <Route path="/tally/:product/customers" element={<TallyCustomersPage />} />
          <Route path="/tally/:product/customers/:id" element={<TallyCustomersPage />} /> /* the Customer Details drawer opens over the list */
          <Route path="/product/:code/dashboard" element={<ProductDashboardPage />} />
          <Route path="/product/:code/customers" element={<ProductCustomersPage />} />
          <Route path="/product/:code/customers/:id" element={<ProductCustomersPage />} />
          <Route path="/mothys/reports/license-expiry" element={<ProductReportPage kind="expiry" />} />
          <Route path="/mothys/reports/customer-details" element={<ProductReportPage kind="details" />} />
          <Route path="/mothys/reports/user-performance" element={<ProductReportPage kind="performance" />} />
          <Route path="/project-management" element={<ProjectListPage />} />
          <Route path="/project-management/add" element={<ProjectFormPage />} />
          <Route path="/project-management/status" element={<IntegrationStatusPage />} />
          <Route path="/project-management/:code/integration" element={<ProjectIntegrationPage />} />
          <Route path="/profile" element={<Navigate to="/dashboard" replace />} />
          <Route path="/reset-password" element={<Navigate to="/dashboard" replace />} />
        </Route>
      </Route>

      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
