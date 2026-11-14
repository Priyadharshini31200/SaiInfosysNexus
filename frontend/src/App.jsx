import { Navigate, Route, Routes, useParams } from 'react-router-dom';
import AppLayout from './layouts/AppLayout';
import { ProtectedRoute, PublicOnlyRoute } from './routes/guards';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import ProjectAddCustomerPage from './pages/ProjectAddCustomerPage';
import ProjectCustomersPage from './pages/ProjectCustomersPage';
import LicenseExpiryReportPage from './pages/LicenseExpiryReportPage';
import UsageReportPage from './pages/UsageReportPage';
import TallyDashboardPage from './pages/tally/TallyDashboardPage';
import TallyUsageListPage from './pages/tally/TallyUsageListPage';
import TallyCustomersPage from './pages/tally/TallyCustomersPage';
import TallyCustomerDetailPage from './pages/tally/TallyCustomerDetailPage';
import TallyReportPage from './pages/tally/TallyReportPage';
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
          <Route path="/dashboard" element={<Dashboard />} />
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
          <Route path="/tally/:product/customers" element={<TallyCustomersPage />} />
          <Route path="/tally/:product/customers/:id" element={<TallyCustomerDetailPage />} />
          <Route path="/profile" element={<Navigate to="/dashboard" replace />} />
          <Route path="/reset-password" element={<Navigate to="/dashboard" replace />} />
        </Route>
      </Route>

      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
