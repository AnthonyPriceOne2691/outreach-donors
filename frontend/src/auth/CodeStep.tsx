/**
 * Второй шаг входа: код из приложения-аутентификатора (23.11.2026).
 *
 * Пароль подошёл, но пропуска ещё нет: сервер ответил вызовом к этому шагу, и без кода вход не
 * состоится — ни на экране, ни через API. Шесть цифр приложения или, если телефона нет, код
 * восстановления — тем же запросом: сервер различает их сам.
 *
 * Неверный код — отказ входа, как неверный пароль: сервер считает попытки и ставит учётку на
 * паузу. Отказ показывается его словами, поле очищается — следующий код в приложении уже другой.
 * Вызов живёт пять минут: устарел — сервер так и скажет, а «Войти заново» вернёт к паролю.
 */

import { Anchor, Button, Group, Stack, Text, TextInput, Title } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useState } from 'react';

import { loginWithCode } from '../api/auth';
import type { SignedIn } from '../api/types';
import { SERVICE_NAME } from '../brand';
import { FIELD_GAP } from '../components/formRhythm';
import { CodeRefusalAlert, useCodeRefusal } from './codeRefusal';

/** Код приложения — шесть цифр; пробел посередине приложения показывают сами. */
export const SIX_DIGITS = /^\d{6}$/;

export function digitsOf(typed: string): string {
  return typed.replace(/[\s-]/g, '');
}

interface Props {
  challenge: string;
  onSignedIn: (opened: SignedIn) => void;
  onRestart: () => void;
}

export function CodeStep({ challenge, onSignedIn, onRestart }: Props) {
  const [recovery, setRecovery] = useState(false);
  const refusal = useCodeRefusal();
  const form = useForm({
    initialValues: { code: '' },
    validate: {
      code: (value) => {
        if (recovery) return value.trim() === '' ? 'Впишите код восстановления' : null;
        return SIX_DIGITS.test(digitsOf(value)) ? null : 'Шесть цифр из приложения';
      },
    },
  });

  const submit = form.onSubmit(async ({ code }) => {
    refusal.clear();
    try {
      onSignedIn(await loginWithCode(challenge, recovery ? code.trim() : digitsOf(code)));
    } catch (error) {
      refusal.show(error);
      form.setFieldValue('code', '');
    }
  });

  const switchKind = () => {
    setRecovery((was) => !was);
    refusal.clear();
    form.reset();
  };

  return (
    <form onSubmit={submit}>
      <Stack gap={FIELD_GAP}>
        <div>
          <Text size="sm" c="dimmed">
            {SERVICE_NAME}
          </Text>
          <Title order={3}>{recovery ? 'Код восстановления' : 'Код из приложения'}</Title>
          <Text size="sm" c="dimmed">
            {recovery
              ? 'Один из десяти кодов, сохранённых при включении. Каждый пускает один раз.'
              : `Пароль подошёл. Откройте приложение-аутентификатор и введите шесть цифр для «${SERVICE_NAME}».`}
          </Text>
        </div>

        <CodeRefusalAlert refusal={refusal} title="Не вошли" />

        {recovery ? (
          <TextInput
            key="recovery"
            label="Код восстановления"
            placeholder="xxxx-xxxx-xxxx-xxxx"
            autoComplete="off"
            spellCheck={false}
            data-autofocus
            {...form.getInputProps('code')}
          />
        ) : (
          <TextInput
            key="code"
            label="Код"
            placeholder="123456"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={7}
            data-autofocus
            {...form.getInputProps('code')}
          />
        )}

        <Button
          type="submit"
          size="md"
          className="press"
          variant="gradient"
          loading={form.submitting}
        >
          Войти
        </Button>
        <Group justify="space-between" gap="xs">
          <Anchor component="button" type="button" size="sm" onClick={switchKind}>
            {recovery ? 'Ввести код из приложения' : 'Нет телефона — код восстановления'}
          </Anchor>
          <Anchor component="button" type="button" size="sm" c="dimmed" onClick={onRestart}>
            Войти заново
          </Anchor>
        </Group>
      </Stack>
    </form>
  );
}
