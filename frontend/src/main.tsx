import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';

const root = document.getElementById('root');
if (root === null) {
  throw new Error('В index.html нет элемента #root — сборка собрана не из этого шаблона');
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
