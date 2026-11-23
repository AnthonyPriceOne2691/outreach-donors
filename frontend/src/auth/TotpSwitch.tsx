/**
 * Включить и выключить свой вход с кодом — формы экрана «Вход с кодом» (`TotpPage`, 23.11.2026).
 *
 * Включить — сначала пароль: украденный пропуск иначе включил бы код на телефон вора и запер бы
 * хозяина. Дальше — ключ в приложение, первый код и коды восстановления (`TotpEnrollment`).
 * Выключить — паролем и кодом: из приложения или кодом восстановления.
 */

import { Alert, PasswordInput, Stack, Text, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useState } from 'react';

import { confirmOwnCode, disableOwnCode, startOwnCode } from '../api/auth';
import { refusalOf } from '../api/client';
import type { TotpSetup } from '../api/types';
import { FIELD_GAP } from '../components/formRhythm';
import { SaveActions } from '../components/SaveActions';
import { notify } from '../notices';
import { useSession } from './AuthProvider';
import { TotpEnrollment } from './TotpEnrollment';

const NEED_PASSWORD = 'Впишите пароль, которым входите';

export function TurnOn({ onLeave }: { onLeave: () => void }) {
  const { accept } = useSession();
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const form = useForm({
    initialValues: { password: '' },
    validate: { password: (value) => (value === '' ? NEED_PASSWORD : null) },
  });

  const submit = form.onSubmit(async ({ password }) => {
    setRefusal(null);
    try {
      setSetup(await startOwnCode(password));
    } catch (error) {
      setRefusal(refusalOf(error));
      form.setFieldValue('password', '');
    }
  });

  if (setup !== null) {
    return (
      <TotpEnrollment
        setup={setup}
        confirm={confirmOwnCode}
        onEnabled={(enabled) => {
          accept(enabled);
        }}
        onDone={() => {
          notify({ message: 'Вход с кодом включён', color: 'green' });
          onLeave();
        }}
      />
    );
  }

  return (
    <form onSubmit={submit}>
      <Stack gap={FIELD_GAP}>
        <Text size="sm">
          Вход будет спрашивать ещё и код из приложения на телефоне: с одним паролем в учётку больше
          не войти. Открытые сеансы на других устройствах закроются.
        </Text>
        {refusal !== null && (
          <Alert color="red" title="Не начали">
            {refusal}
          </Alert>
        )}
        <PasswordInput
          label="Ваш пароль"
          description="Включение подтверждается паролем, которым вы входите"
          autoComplete="current-password"
          data-autofocus
          {...form.getInputProps('password')}
        />
        <SaveActions onCancel={onLeave} saving={form.submitting} locked={null} saveLabel="Дальше" />
      </Stack>
    </form>
  );
}

export function TurnOff({ onLeave }: { onLeave: () => void }) {
  const { refresh } = useSession();
  const [refusal, setRefusal] = useState<string | null>(null);
  const form = useForm({
    initialValues: { password: '', code: '' },
    validate: {
      password: (value) => (value === '' ? NEED_PASSWORD : null),
      code: (value) => (value.trim() === '' ? 'Код из приложения или код восстановления' : null),
    },
  });

  const submit = form.onSubmit(async ({ password, code }) => {
    setRefusal(null);
    try {
      await disableOwnCode(password, code.trim());
      await refresh();
      notify({ message: 'Вход с кодом выключен', color: 'green' });
      onLeave();
    } catch (error) {
      setRefusal(refusalOf(error));
      form.setValues({ password: '', code: '' });
    }
  });

  return (
    <form onSubmit={submit}>
      <Stack gap={FIELD_GAP}>
        <Text size="sm">
          Выключить — паролем и кодом. Коды восстановления пропадут вместе с ключом; включить заново
          можно здесь же.
        </Text>
        {refusal !== null && (
          <Alert color="red" title="Не выключили">
            {refusal}
          </Alert>
        )}
        <PasswordInput
          label="Ваш пароль"
          autoComplete="current-password"
          {...form.getInputProps('password')}
        />
        <TextInput
          label="Код"
          description="Шесть цифр из приложения или код восстановления"
          autoComplete="one-time-code"
          {...form.getInputProps('code')}
        />
        <SaveActions
          onCancel={onLeave}
          saving={form.submitting}
          locked={null}
          saveLabel="Выключить"
          danger
        />
      </Stack>
    </form>
  );
}
