/**
 * Обязательное включение входа с кодом — сразу после пароля (23.11.2026).
 *
 * Роль учётки требует код (`AUTH_TOTP_REQUIRED_ROLES` сервера), а он не включён: пропуска без
 * кода сервер не выдаст, и вход продолжается здесь, по вызову из ответа на пароль. Включение
 * начинается кнопкой, а не само: начатое включение кладёт на сервер новый ключ, и повтор
 * запроса при перерисовке подменил бы ключ, который человек уже переписывает.
 *
 * **Пропуск принимает вход, а не шаг** — как у кода из приложения (`CodeStep`): шаг сеанса не
 * трогает. Код включён — пропуск из ответа уходит наверх сразу (`onSignedIn`), ещё до экрана кодов
 * восстановления: прежние пропуска сервер погасил, и фоновый запрос со старым выбросил бы на вход.
 * Войти — когда коды сохранены (`onDone`).
 */

import { Alert, Anchor, Button, Stack, Text, Title } from '@mantine/core';
import { useState } from 'react';

import { confirmEnrollment, startEnrollment } from '../api/auth';
import { refusalOf } from '../api/client';
import type { Me, SignedIn, TotpSetup } from '../api/types';
import { SERVICE_NAME } from '../brand';
import { FIELD_GAP } from '../components/formRhythm';
import { TotpEnrollment } from './TotpEnrollment';

interface Props {
  challenge: string;
  /** Код включён — пропуск от сервера: принять сразу, до экрана кодов восстановления. */
  onSignedIn: (opened: SignedIn) => void;
  /** Коды восстановления сохранены — войти. */
  onDone: (user: Me) => void;
  onRestart: () => void;
}

export function EnrollStep({ challenge, onSignedIn, onDone, onRestart }: Props) {
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const begin = async () => {
    setStarting(true);
    setRefusal(null);
    try {
      setSetup(await startEnrollment(challenge));
    } catch (error) {
      setRefusal(refusalOf(error));
    } finally {
      setStarting(false);
    }
  };

  return (
    <Stack gap={FIELD_GAP}>
      <div>
        <Text size="sm" c="dimmed">
          {SERVICE_NAME}
        </Text>
        <Title order={3}>Вход с кодом</Title>
        <Text size="sm" c="dimmed">
          Пароль подошёл. Для вашей роли нужен ещё код из приложения на телефоне — включите его, это
          пара минут. Дальше вход — пароль и код.
        </Text>
      </div>

      {refusal !== null && (
        <Alert color="red" title="Не начали">
          {refusal}
        </Alert>
      )}

      {setup === null ? (
        <>
          <Button
            size="md"
            className="press"
            variant="gradient"
            loading={starting}
            onClick={() => void begin()}
          >
            Включить вход с кодом
          </Button>
          <Anchor component="button" type="button" size="sm" c="dimmed" onClick={onRestart}>
            Войти заново
          </Anchor>
        </>
      ) : (
        <TotpEnrollment
          setup={setup}
          confirm={(code) => confirmEnrollment(challenge, code)}
          onEnabled={onSignedIn}
          onDone={(enabled) => onDone(enabled.user)}
        />
      )}
    </Stack>
  );
}
