import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';
import './styles/glass.css';

import { ColorSchemeScript } from '@mantine/core';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';

const root = document.getElementById('root');
if (root === null) {
  throw new Error('В index.html нет элемента #root — сборка собрана не из этого шаблона');
}

createRoot(root).render(
  <StrictMode>
    {/* Ставит тему первым же кадром. Без этого человек с тёмной системой
        видит вспышку белого полотна на всю страницу перед тем, как тема
        применится. */}
    <ColorSchemeScript defaultColorScheme="auto" />
    <App />
  </StrictMode>,
);
