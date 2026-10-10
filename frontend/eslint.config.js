import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'node_modules'] },
  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      '@typescript-eslint/consistent-type-imports': 'error',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
  {
    // Уведомления — только через `notify()` (src/notices.ts): красное без явного срока
    // не гаснет само. Прямой `notifications.show` обходил бы это правило — так и было
    // в 30 файлах до 10.10.2026. Тестам можно: они чистят и подслушивают уведомления.
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['src/notices.ts', 'src/test/**', 'src/**/*.test.{ts,tsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: '@mantine/notifications',
              importNames: ['notifications'],
              message:
                'Уведомления — через notify() из src/notices.ts: красное без срока не гаснет само.',
            },
          ],
        },
      ],
    },
  },
);
