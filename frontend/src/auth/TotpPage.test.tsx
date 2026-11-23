/**
 * «Вход с кодом» своей учётки (23.11.2026): включить по своей воле — паролем, ключом и первым
 * кодом; выключить — паролем и кодом; обязательный — не выключить.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { Me } from '../api/types';
import { OPERATOR, TOKEN_KEY, signedIn } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const ON: Me = { ...OPERATOR, totp_enabled: true };

function asSignedIn(user: Me) {
  localStorage.setItem(TOKEN_KEY, 'пропуск.старый');
  return { 'GET /api/auth/me': { body: user } };
}

describe('вход с кодом своей учётки', () => {
  it('включить: пароль, ключ, первый код — новый пропуск и коды восстановления', async () => {
    const codes = Array.from(
      { length: 10 },
      (_, n) => `abcd-efgh-jkmn-pq${String(n).padStart(2, '0')}`,
    );
    const recorded = serve({
      ...asSignedIn(OPERATOR),
      'POST /api/auth/totp/setup': {
        body: {
          secret: 'JBSWY3DPEHPK3PXP', // pragma: allowlist secret — пример ключа, не секрет
          uri: 'otpauth://totp/x',
          issuer: 'Parsing and Prices',
          account: OPERATOR.email,
        },
      },
      'POST /api/auth/totp/confirm': {
        body: { ...signedIn(ON), recovery_codes: codes, note: 'Показаны один раз.' },
      },
    });
    renderWith(<AppRoutes />, '/account/code');
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText('Ваш пароль'), 'пароль-для-теста');
    await user.click(screen.getByRole('button', { name: 'Дальше' }));
    await user.type(await screen.findByLabelText('Код из приложения'), '111222');
    await user.click(screen.getByRole('button', { name: 'Включить' }));

    expect(await screen.findByText('abcd-efgh-jkmn-pq00')).toBeVisible();
    expect(localStorage.getItem(TOKEN_KEY)).toBe(signedIn(ON).token);
    expect(recorded.calls.map((call) => [call.path, call.body])).toEqual(
      expect.arrayContaining([
        ['/api/auth/totp/setup', { password: 'пароль-для-теста' }],
        ['/api/auth/totp/confirm', { code: '111222' }],
      ]),
    );
  });

  it('выключить: пароль и код', async () => {
    const recorded = serve({
      ...asSignedIn(ON),
      'POST /api/auth/totp/disable': { body: OPERATOR },
    });
    renderWith(<AppRoutes />, '/account/code');
    const user = userEvent.setup();

    expect(await screen.findByText('включён')).toBeVisible();
    await user.type(screen.getByLabelText('Ваш пароль'), 'пароль-для-теста');
    await user.type(screen.getByLabelText('Код'), '123456');
    await user.click(screen.getByRole('button', { name: 'Выключить' }));

    expect(recorded.calls.find((call) => call.path === '/api/auth/totp/disable')?.body).toEqual({
      password: 'пароль-для-теста',
      code: '123456',
    });
  });

  it('обязательный код не выключить — сказано, что делать', async () => {
    serve(asSignedIn({ ...ON, totp_required: true }));
    renderWith(<AppRoutes />, '/account/code');

    expect(await screen.findByText(/попросите админа сбросить вход с кодом/)).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Выключить' })).not.toBeInTheDocument();
  });
});
