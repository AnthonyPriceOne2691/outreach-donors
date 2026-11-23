import type { Me, SignedIn, TotpEnabled, TotpSetup } from './types';
import { request } from './client';

/** Вход парой. У учётки с кодом пропуска в ответе нет: сервер отвечает отказом с признаком
 *  второго шага — запрос бросает `SecondStepError` с вызовом к нему (23.11.2026). */
export function login(email: string, password: string): Promise<SignedIn> {
  return request<SignedIn>('/auth/login', {
    method: 'POST',
    body: { email, password },
    anonymous: true,
  });
}

export function fetchMe(): Promise<Me> {
  return request<Me>('/auth/me');
}

/** Смена своего пароля. Ответ — новый пропуск с карточкой, как у входа: пропуска,
 *  выданные до смены, сервер больше не принимает (проверка прода 10.10.2026). */
export function changePassword(current: string, next: string): Promise<SignedIn> {
  return request<SignedIn>('/auth/password', {
    method: 'POST',
    body: { current, new: next },
  });
}

/** Выход: сервер закрывает этот пропуск до конца его срока (аудит 20.11.2026) — копия
 *  пропуска после выхода больше не пускает. Ответ — 204 без тела. */
export function logout(): Promise<void> {
  return request<void>('/auth/logout', { method: 'POST' });
}

/** Второй шаг входа: код из приложения или код восстановления по вызову → пропуск. */
export function loginWithCode(challenge: string, code: string): Promise<SignedIn> {
  return request<SignedIn>('/auth/login/code', {
    method: 'POST',
    body: { challenge, code },
    anonymous: true,
  });
}

/** Роль требует код, а он не включён: секрет для приложения по вызову входа. */
export function startEnrollment(challenge: string): Promise<TotpSetup> {
  return request<TotpSetup>('/auth/login/enroll', {
    method: 'POST',
    body: { challenge },
    anonymous: true,
  });
}

/** Первый код по вызову → код включён, вход состоялся, коды восстановления — один раз. */
export function confirmEnrollment(challenge: string, code: string): Promise<TotpEnabled> {
  return request<TotpEnabled>('/auth/login/enroll/confirm', {
    method: 'POST',
    body: { challenge, code },
    anonymous: true,
  });
}

/** Включить свой код: сначала пароль — украденный пропуск иначе включил бы код вору. */
export function startOwnCode(password: string): Promise<TotpSetup> {
  return request<TotpSetup>('/auth/totp/setup', { method: 'POST', body: { password } });
}

/** Первый код по начатому секрету. Прежние пропуска учётки гаснут — в ответе новый. */
export function confirmOwnCode(code: string): Promise<TotpEnabled> {
  return request<TotpEnabled>('/auth/totp/confirm', { method: 'POST', body: { code } });
}

/** Выключить свой код — паролем и кодом. Роль требует код — сервер откажет словами. */
export function disableOwnCode(password: string, code: string): Promise<Me> {
  return request<Me>('/auth/totp/disable', { method: 'POST', body: { password, code } });
}
