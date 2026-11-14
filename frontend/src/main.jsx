import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { TallyAccessProvider } from './context/TallyAccessContext';
import App from './App';
import './styles/global.css';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, '') || '/'}>
      <AuthProvider>
        <TallyAccessProvider>
          <App />
        </TallyAccessProvider>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
