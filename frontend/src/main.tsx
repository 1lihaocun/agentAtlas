import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router/dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import { queryClient } from './app/query-client';
import { router } from './app/router';
import './app/styles.css';

createRoot(document.getElementById('root')!).render(<StrictMode>
  <ConfigProvider theme={{ token: { colorPrimary: '#28613f', fontSize: 12, borderRadius: 6 },
    components: { Dropdown: { controlItemBgActive: '#eef4ec', controlItemBgActiveHover: '#e5eee2' } } }}>
    <QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>
  </ConfigProvider>
</StrictMode>);
