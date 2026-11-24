/**
 * Вход с кодом глазами человека (23.11.2026): пароль, затем код — или включение кода, если роль
 * его требует. Сервер заменён записанными ответами: проверяется, что показано и что ушло.
 */

import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { Me, TotpEnabled, TotpSetup } from '../api/types';
import { ADMIN, HOME_ROUTES, TOKEN_KEY, card, signedIn } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { theme } from '../theme';
import { EnrollStep } from './EnrollStep';
import { codesText } from './RecoveryCodes';
import { keyGroups } from './TotpEnrollment';

const WITH_CODE: Me = { ...ADMIN, totp_enabled: true };
/** Учётка, чья роль требует код, — сразу после включения. */
const ENROLLED: Me = { ...ADMIN, totp_enabled: true, totp_required: true };

const SETUP: TotpSetup = {
  secret: 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP', // pragma: allowlist secret — пример ключа, не секрет
  uri: 'otpauth://totp/Parsing%20and%20Prices:admin?secret=JBSW',
  issuer: 'Parsing and Prices',
  account: 'админ@site.com',
};

function secondStep(step: 'code' | 'enroll') {
  return {
    status: 403,
    headers: { 'X-Access-Refusal': 'second-step' },
    body: {
      detail: 'Пароль подошёл — нужен код из приложения-аутентификатора',
      step,
      challenge: `вызов-${step}`,
      expires_in_minutes: 5,
    },
  };
}

const CODES = Array.from(
  { length: 10 },
  (_, n) => `k3m9-x2qa-77ab-cd${String(n).padStart(2, '0')}`,
);

function enabled(user: Me): TotpEnabled {
  return {
    ...signedIn(user),
    recovery_codes: CODES,
    note: 'Коды восстановления показаны один раз.',
  };
}

async function enterPassword() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText('Почта'), 'админ@site.com');
  await user.type(screen.getByLabelText('Пароль'), 'пароль-для-теста');
  await user.click(screen.getByRole('button', { name: 'Войти' }));
  return user;
}

describe('вход: второй шаг — код', () => {
  it('после пароля спрашивает код и пускает только с ним', async () => {
    const recorded = serve({
      'POST /api/auth/login': secondStep('code'),
      'POST /api/auth/login/code': { body: signedIn(WITH_CODE) },
      ...HOME_ROUTES,
    });
    renderWith(<AppRoutes />, '/login');

    const user = await enterPassword();

    expect(await screen.findByText('Код из приложения')).toBeInTheDocument();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
    await user.type(screen.getByLabelText('Код'), '123 456');
    await user.click(screen.getByRole('button', { name: 'Войти' }));

    expect(await screen.findByText(/Вошли как/)).toBeInTheDocument();
    expect(localStorage.getItem(TOKEN_KEY)).toBe(signedIn(WITH_CODE).token);
    expect(recorded.calls[1]).toMatchObject({
      path: '/api/auth/login/code',
      body: { challenge: 'вызов-code', code: '123456' },
      token: null,
    });
  });

  it('неверный код — отказ словами сервера, поле чистое', async () => {
    serve({
      'POST /api/auth/login': secondStep('code'),
      'POST /api/auth/login/code': {
        status: 400,
        body: { detail: 'Код не подошёл — введите новый из приложения' },
      },
    });
    renderWith(<AppRoutes />, '/login');

    const user = await enterPassword();
    await user.type(await screen.findByLabelText('Код'), '000000');
    await user.click(screen.getByRole('button', { name: 'Войти' }));

    expect(await screen.findByText('Код не подошёл — введите новый из приложения')).toBeVisible();
    expect(screen.getByLabelText('Код')).toHaveValue('');
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it('без телефона — код восстановления тем же запросом', async () => {
    const recorded = serve({
      'POST /api/auth/login': secondStep('code'),
      'POST /api/auth/login/code': { body: signedIn(WITH_CODE) },
      ...HOME_ROUTES,
    });
    renderWith(<AppRoutes />, '/login');

    const user = await enterPassword();
    await user.click(
      await screen.findByRole('button', { name: 'Нет телефона — код восстановления' }),
    );
    await user.type(screen.getByLabelText('Код восстановления'), 'k3m9-x2qa-77ab-cd00');
    await user.click(screen.getByRole('button', { name: 'Войти' }));

    expect(await screen.findByText(/Вошли как/)).toBeInTheDocument();
    expect(recorded.calls[1]?.body).toEqual({
      challenge: 'вызов-code',
      code: 'k3m9-x2qa-77ab-cd00',
    });
  });

  it('шесть цифр проверяются до сервера; «Войти заново» возвращает к паролю', async () => {
    const recorded = serve({ 'POST /api/auth/login': secondStep('code') });
    renderWith(<AppRoutes />, '/login');

    const user = await enterPassword();
    await user.type(await screen.findByLabelText('Код'), '12');
    await user.click(screen.getByRole('button', { name: 'Войти' }));

    expect(await screen.findByText('Шесть цифр из приложения')).toBeInTheDocument();
    expect(recorded.calls).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Войти заново' }));
    expect(await screen.findByLabelText('Почта')).toBeVisible();
  });
});

describe('вход: роль требует код — включение', () => {
  it('ключ группами, ссылка в приложение, первый код, коды восстановления один раз', async () => {
    const recorded = serve({
      'POST /api/auth/login': secondStep('enroll'),
      'POST /api/auth/login/enroll': { body: SETUP },
      'POST /api/auth/login/enroll/confirm': { body: enabled(ENROLLED) },
      ...HOME_ROUTES,
    });
    renderWith(<AppRoutes />, '/login');

    const user = await enterPassword();
    await user.click(await screen.findByRole('button', { name: 'Включить вход с кодом' }));

    expect(await screen.findByText('JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Открыть в приложении' })).toHaveAttribute(
      'href',
      'otpauth://totp/Parsing%20and%20Prices:admin?secret=JBSW',
    );
    await user.type(screen.getByLabelText('Код из приложения'), '654321');
    await user.click(screen.getByRole('button', { name: 'Включить' }));

    expect(await screen.findByText('k3m9-x2qa-77ab-cd00')).toBeVisible();
    expect(screen.getAllByText(/^k3m9-/)).toHaveLength(10);
    // Код — одной строкой: по дефисам он рвался на две, и его переписывали с ошибкой (24.11.2026).
    expect(screen.getByText('k3m9-x2qa-77ab-cd00')).toHaveStyle({ whiteSpace: 'nowrap' });
    expect(screen.getByRole('button', { name: 'Скачать .txt' })).toBeVisible();
    // Пропуск взят сразу: прежние сервер погасил, а коды человек ещё читает. Входа — ещё нет.
    expect(localStorage.getItem(TOKEN_KEY)).toBe(signedIn(ADMIN).token);
    expect(screen.queryByText(/Вошли как/)).not.toBeInTheDocument();
    expect(recorded.calls[2]).toMatchObject({
      path: '/api/auth/login/enroll/confirm',
      body: { challenge: 'вызов-enroll', code: '654321' },
    });

    await user.click(screen.getByRole('button', { name: 'Я сохранил' }));
    expect(await screen.findByText(/Вошли как/)).toBeInTheDocument();
  });

  it('шаг отдаёт пропуск наверх: принят сразу после включения, вход — после «Я сохранил»', async () => {
    serve({
      'POST /api/auth/login/enroll': { body: SETUP },
      'POST /api/auth/login/enroll/confirm': { body: enabled(ENROLLED) },
    });
    const happened: string[] = [];
    // Без `AuthProvider`: шаг, который берёт пропуск сам (`useSession`), здесь бы упал.
    render(
      <MantineProvider theme={theme}>
        <EnrollStep
          challenge="вызов-enroll"
          onSignedIn={(opened) => {
            happened.push(`принят ${opened.token}`);
          }}
          onDone={(user) => {
            happened.push(`вошёл ${user.email}`);
          }}
          onRestart={() => {
            happened.push('заново');
          }}
        />
      </MantineProvider>,
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Включить вход с кодом' }));
    await user.type(await screen.findByLabelText('Код из приложения'), '654321');
    await user.click(screen.getByRole('button', { name: 'Включить' }));

    expect(await screen.findByText('k3m9-x2qa-77ab-cd00')).toBeVisible();
    expect(happened).toEqual([`принят ${signedIn(ENROLLED).token}`]);
    await user.click(screen.getByRole('button', { name: 'Я сохранил' }));
    expect(happened).toEqual([`принят ${signedIn(ENROLLED).token}`, `вошёл ${ENROLLED.email}`]);
  });

  it('отказ начать — словами сервера', async () => {
    serve({
      'POST /api/auth/login': secondStep('enroll'),
      'POST /api/auth/login/enroll': {
        status: 409,
        body: { detail: 'Вход с кодом на сервере не настроен: нет ключа шифрования AUTH_TOTP_KEY' },
      },
    });
    renderWith(<AppRoutes />, '/login');

    const user = await enterPassword();
    await user.click(await screen.findByRole('button', { name: 'Включить вход с кодом' }));

    expect(await screen.findByText(/нет ключа шифрования AUTH_TOTP_KEY/)).toBeVisible();
  });
});

describe('кусочки экрана включения', () => {
  it('ключ — группами по четыре', () => {
    expect(keyGroups('ABCDEFGHIJ')).toBe('ABCD EFGH IJ');
  });

  it('файл кодов называет учётку и как ими пользоваться', () => {
    const text = codesText(['a', 'b'], 'ivan@site.com');
    expect(text).toContain('ivan@site.com');
    expect(text).toContain('один раз');
    expect(text.split('\n')).toContain('b');
  });

  it('учётка с кодом в списке — карточкой с отметкой', () => {
    expect(card(WITH_CODE).totp_enabled).toBe(true);
  });
});

describe('старый ответ без шага — как раньше', () => {
  it('отказ с признаком шага, но без вызова — обычный отказ входа', async () => {
    serve({
      'POST /api/auth/login': {
        status: 403,
        headers: { 'X-Access-Refusal': 'second-step' },
        body: { detail: 'Нужен второй шаг' },
      },
    });
    renderWith(<AppRoutes />, '/login');

    await enterPassword();

    await waitFor(() => expect(screen.getByText('Нужен второй шаг')).toBeVisible());
    expect(screen.getByLabelText('Почта')).toBeVisible();
  });
});
