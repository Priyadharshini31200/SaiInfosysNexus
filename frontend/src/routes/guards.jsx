import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import FullPageLoader from '../components/FullPageLoader';

/** Renders child routes only for a verified session; otherwise sends the user to Login. */
export function ProtectedRoute() {
  const { status } = useAuth();
  const location = useLocation();
  if (status === 'loading') return <FullPageLoader label="Checking your session…" />;
  if (status !== 'authenticated') return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <Outlet />;
}

/** Login is only for anonymous visitors; signed-in users go straight to the Dashboard. */
export function PublicOnlyRoute() {
  const { status } = useAuth();
  if (status === 'loading') return <FullPageLoader label="Loading…" />;
  if (status === 'authenticated') return <Navigate to="/dashboard" replace />;
  return <Outlet />;
}
