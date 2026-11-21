import { useLocation } from 'react-router-dom';

/** The same project pages serve /project-management/* (legacy menu, on hold) and /universe/* (UNIVERSE section). */
export function useIntegrationPaths() {
  const { pathname } = useLocation();
  if (pathname.startsWith('/universe')) {
    return { list: '/universe/projects', add: '/universe/projects/add', status: '/universe/api?tab=logs', open: (code) => `/universe/api?project=${code}` };
  }
  return { list: '/project-management', add: '/project-management/add', status: '/project-management/status', open: (code) => `/project-management/${code}/integration` };
}
